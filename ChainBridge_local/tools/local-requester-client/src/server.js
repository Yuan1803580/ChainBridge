import { timingSafeEqual } from "node:crypto";
import { existsSync } from "node:fs";
import http from "node:http";

import { formatEther, formatUnits, parseEther, parseUnits } from "ethers";

import { CoordinatorClient, PlatformClient } from "./api.js";
import {
  assertTrustedDeployment,
  balanceSnapshot,
  executeFunding,
  verifyFaucetEvidence,
} from "./contracts.js";
import { loadConfig } from "./config.js";
import { validatePlan, verifyTermsHash } from "./planning.js";
import { isResumableRun, loadRunState, retryDelay, saveRunState } from "./run-state.js";
import { newRequestId, sleep } from "./utils.js";
import { unlockWalletWithPassword, walletAddress } from "./wallet.js";

const TERMINAL_STATUSES = new Set(["COMPLETED", "FAILED", "FAILED_REFUNDED", "CANCELLED"]);
const MAX_BODY_BYTES = 64 * 1024;

function now() {
  return new Date().toISOString();
}

function sendJson(response, status, body) {
  const value = JSON.stringify(body);
  response.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Content-Length": Buffer.byteLength(value),
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
  });
  response.end(value);
}

function sendHtml(response, status, body) {
  response.writeHead(status, {
    "Content-Type": "text/html; charset=utf-8",
    "Content-Length": Buffer.byteLength(body),
    "Cache-Control": "no-store",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
  });
  response.end(body);
}

function sameSecret(left, right) {
  const leftValue = Buffer.from(left || "");
  const rightValue = Buffer.from(right || "");
  return leftValue.length === rightValue.length && timingSafeEqual(leftValue, rightValue);
}

async function readRawBody(request) {
  const chunks = [];
  let size = 0;
  for await (const chunk of request) {
    size += chunk.length;
    if (size > MAX_BODY_BYTES) throw Object.assign(new Error("Request body is too large"), { statusCode: 413 });
    chunks.push(chunk);
  }
  return Buffer.concat(chunks).toString("utf8");
}

async function readBody(request) {
  const raw = await readRawBody(request);
  if (!raw) return {};
  try {
    return JSON.parse(raw);
  } catch {
    throw Object.assign(new Error("Request body must be valid JSON"), { statusCode: 400 });
  }
}

async function trustedContext(config) {
  const platform = new PlatformClient(config);
  const publicConfig = await platform.publicConfig();
  await assertTrustedDeployment(publicConfig, config.trustedDeploymentFile);
  return { platform, publicConfig };
}

function positiveDeficit(current, target) {
  return current < target ? target - current : 0n;
}

async function ensureWalletFunds(config, tokenTarget, nativeTarget) {
  const { platform, publicConfig } = await trustedContext(config);
  const address = await walletAddress(config.walletFile);
  const before = await balanceSnapshot(publicConfig, address);
  const tokenTargetUnits = parseUnits(String(tokenTarget), before.tokenDecimals);
  const nativeTargetWei = parseEther(String(nativeTarget));
  const tokenDeficit = positiveDeficit(BigInt(before.tokenUnits), tokenTargetUnits);
  const nativeDeficit = positiveDeficit(BigInt(before.nativeWei), nativeTargetWei);
  if (tokenDeficit === 0n && nativeDeficit === 0n) {
    return { funded: false, publicConfig, balances: before, faucet: null };
  }

  const tokenAmount = tokenDeficit > 0n
    ? formatUnits(tokenDeficit, before.tokenDecimals)
    : formatUnits(1n, before.tokenDecimals);
  const nativeAmount = formatEther(nativeDeficit);
  const faucet = await platform.faucet({
    requestId: newRequestId("requester-auto-fund"),
    walletAddress: address,
    tokenAmount,
    nativeAmount,
  });
  const verified = await verifyFaucetEvidence(publicConfig, address, faucet, before);
  return { funded: true, publicConfig, balances: verified.after, faucet };
}

async function walletView(config, startup, signerReady) {
  if (!existsSync(config.walletFile)) {
    return {
      status: "MISSING",
      chainId: config.chainId,
      signerReady,
      startup,
    };
  }
  const address = await walletAddress(config.walletFile);
  const { publicConfig } = await trustedContext(config);
  return {
    status: "READY",
    mode: config.mode,
    address,
    chainId: publicConfig.chainId,
    chainName: publicConfig.chainName,
    sourceToken: publicConfig.sourceToken,
    taskEscrow: publicConfig.taskEscrow,
    gasTreasury: publicConfig.gasTreasury,
    deploymentId: publicConfig.deploymentId,
    balances: await balanceSnapshot(publicConfig, address),
    signerReady,
    startup,
  };
}

async function fundWorkflow(config, conversationId, password, coordinatorProfileId = "") {
  if (!existsSync(config.walletFile)) throw Object.assign(new Error("Requester encrypted wallet does not exist"), { statusCode: 409 });
  if (!password) {
    throw Object.assign(
      new Error("Requester 钱包尚未解锁，请先打开 http://127.0.0.1:8400/unlock"),
      { statusCode: 409 },
    );
  }
  const coordinator = new CoordinatorClient(config, coordinatorProfileId);
  const platform = new PlatformClient(config);
  const conversation = await coordinator.conversation(conversationId);
  validatePlan(conversation);
  const address = await walletAddress(config.walletFile);
  const created = await coordinator.createWorkflow(conversationId, address);
  const workflow = created.workflow;
  verifyTermsHash(conversation, workflow);

  const { publicConfig } = await ensureWalletFunds(
    config,
    workflow.budget,
    config.autoFundNativeTarget,
  );
  if (workflow.fundingStatus === "CONFIRMED") {
    return {
      reused: true,
      wallet: await walletView(config, { status: "READY" }, true),
      workflow,
      funding: {
        approveTxHash: workflow.approveTxHash,
        lockTxHash: workflow.lockTxHash,
        sourceGasTxHash: workflow.sourceGasTxHash,
      },
    };
  }

  const signer = await unlockWalletWithPassword(config.walletFile, password);
  const funding = await executeFunding(
    publicConfig,
    signer,
    workflow,
    {
      approveTxHash: workflow.approveTxHash,
      lockTxHash: workflow.lockTxHash,
      sourceGasTxHash: workflow.sourceGasTxHash,
    },
  );
  const confirmation = await platform.confirmFunding(workflow.id, {
    walletAddress: address,
    termsHash: workflow.termsHash,
    approveTxHash: funding.approveTxHash,
    lockTxHash: funding.lockTxHash,
    sourceGasTxHash: funding.sourceGasTxHash,
    sourceGasAmount: funding.sourceGasAmount,
    deploymentId: publicConfig.deploymentId,
    verificationMode: "RPC",
  });
  await coordinator.coordinate(conversationId, 1);
  return {
    reused: false,
    wallet: await walletView(config, { status: "READY" }, true),
    workflow: confirmation.workflow,
    funding,
  };
}

async function main() {
  const config = await loadConfig();
  if (!config.serviceToken || config.serviceToken.length < 16) {
    throw new Error("LOCAL_REQUESTER_API_TOKEN must contain at least 16 characters");
  }
  if (config.serviceHost !== "127.0.0.1" && config.serviceHost !== "localhost") {
    throw new Error("Requester service may only bind to the local loopback interface");
  }

  const runs = await loadRunState(config.runsFile);
  const activeDrivers = new Set();
  let persistenceQueue = Promise.resolve();
  let fundingQueue = Promise.resolve();
  let activeWalletPassword = process.env.REQUESTER_WALLET_PASSWORD || "";
  let failedUnlockAttempts = 0;
  const startup = { status: "PENDING", checkedAt: null, error: null };

  function persistRuns() {
    persistenceQueue = persistenceQueue
      .catch(() => undefined)
      .then(() => saveRunState(config.runsFile, runs));
    return persistenceQueue;
  }

  async function driveWorkflow(conversationId) {
    if (activeDrivers.has(conversationId)) return;
    activeDrivers.add(conversationId);
    const state = runs.get(conversationId);
    const coordinator = new CoordinatorClient(config, state?.coordinatorProfileId || "");
    const platform = new PlatformClient(config);
    try {
      while (isResumableRun(state)) {
        try {
          const conversation = await coordinator.conversation(conversationId);
          if (!conversation.workflowId) throw new Error("Conversation has no funded workflow");
          let workflow = await platform.workflow(conversation.workflowId);
          if (workflow.fundingStatus !== "CONFIRMED") throw new Error("Workflow funding is not confirmed");
          state.workflowId = workflow.id;
          if (!TERMINAL_STATUSES.has(workflow.status)) {
            // Coordinator Core's durable recovery scheduler advances active
            // workflows. The Requester observes status only; repeatedly
            // issuing coordinate commands would create unbounded idempotency
            // records while an Agent or protocol job is still running.
            state.lastAction = "waiting_for_coordinator_core";
          } else {
            state.lastAction = "terminal_state_observed";
          }
          state.cycle = Number(state.cycle || 0) + 1;
          state.workflowStatus = workflow.status;
          state.status = TERMINAL_STATUSES.has(workflow.status) ? "COMPLETED"
            : state.lastAction === "waiting_for_agent" ? "WAITING_RETRY" : "RUNNING";
          state.error = null;
          state.retryCount = 0;
          state.nextRetryAt = null;
          state.updatedAt = now();
          if (state.status === "COMPLETED") state.completedAt = now();
          await persistRuns();
          if (state.status === "COMPLETED") break;
          await sleep(config.pollIntervalMs);
        } catch (error) {
          state.status = "WAITING_RETRY";
          state.error = error instanceof Error ? error.message : String(error);
          state.retryCount = Number(state.retryCount || 0) + 1;
          const delay = retryDelay(state.retryCount, config.retryBaseMs, config.retryMaxMs);
          state.nextRetryAt = new Date(Date.now() + delay).toISOString();
          state.updatedAt = now();
          await persistRuns();
          await sleep(delay);
        }
      }
    } finally {
      activeDrivers.delete(conversationId);
      await persistRuns();
    }
  }

  const server = http.createServer(async (request, response) => {
    try {
      const url = new URL(request.url || "/", `http://${config.serviceHost}:${config.servicePort}`);
      if (request.method === "GET" && url.pathname === "/health") {
        sendJson(response, 200, {
          service: "local-requester-wallet-service",
          runtimeId: config.runtimeId,
          status: "ok",
          mode: config.mode,
          walletConfigured: existsSync(config.walletFile),
          signerReady: Boolean(activeWalletPassword),
          startup,
        });
        return;
      }

      if (request.method === "GET" && url.pathname === "/unlock") {
        sendHtml(response, 200, `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>解锁 Requester 钱包</title><style>body{margin:0;background:#f7f7f5;color:#171717;font-family:system-ui,sans-serif}.wrap{max-width:520px;margin:12vh auto;padding:24px}.panel{border:1px solid #d8d8d3;background:white;padding:24px;border-radius:6px}h1{font-size:20px;margin:0 0 8px}p{font-size:13px;line-height:1.7;color:#60605b}label{display:block;font-size:12px;font-weight:600;margin-top:20px}input{box-sizing:border-box;width:100%;height:42px;margin-top:7px;border:1px solid #c9c9c3;border-radius:4px;padding:0 11px;font-size:14px}button{width:100%;height:42px;margin-top:14px;border:0;border-radius:4px;background:#166534;color:white;font-size:13px;font-weight:700;cursor:pointer}.state{margin-top:16px;border-top:1px solid #e5e5e0;padding-top:14px;font-family:ui-monospace,monospace;font-size:11px;color:#666}</style></head><body><main class="wrap"><section class="panel"><h1>解锁独立 Requester 钱包</h1><p>密码只提交到本机 127.0.0.1 上的 Requester 进程，并仅保存在当前进程内存中。</p><form method="post" action="/unlock"><label for="password">钱包密码</label><input id="password" name="password" type="password" minlength="10" required autocomplete="current-password"><button type="submit">解锁钱包</button></form><div class="state">${activeWalletPassword ? "当前状态：已解锁" : "当前状态：未解锁"}</div></section></main></body></html>`);
        return;
      }
      if (request.method === "POST" && url.pathname === "/unlock") {
        const expectedOrigin = `http://${config.serviceHost}:${config.servicePort}`;
        const origin = String(request.headers.origin || "");
        if (origin && origin !== expectedOrigin) {
          sendHtml(response, 403, "<!doctype html><meta charset=\"utf-8\"><p>拒绝跨站解锁请求。</p>");
          return;
        }
        if (failedUnlockAttempts >= 5) {
          sendHtml(response, 429, "<!doctype html><meta charset=\"utf-8\"><p>失败次数过多，请重启 Requester 服务后再试。</p>");
          return;
        }
        const form = new URLSearchParams(await readRawBody(request));
        const password = String(form.get("password") || "");
        try {
          await unlockWalletWithPassword(config.walletFile, password);
          activeWalletPassword = password;
          failedUnlockAttempts = 0;
          sendHtml(response, 200, "<!doctype html><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><body style=\"font-family:system-ui;margin:12vh auto;max-width:520px;padding:24px\"><h1 style=\"font-size:20px\">钱包已解锁</h1><p>可以返回平台创建并锁定工作流预算。关闭或重启 Requester 服务后需要重新解锁。</p><a href=\"http://127.0.0.1:3100/workflows/new\">返回工作流页面</a></body>");
        } catch {
          failedUnlockAttempts += 1;
          sendHtml(response, 401, "<!doctype html><meta charset=\"utf-8\"><p>钱包密码不正确。<a href=\"/unlock\">返回重试</a></p>");
        }
        return;
      }

      const token = String(request.headers.authorization || "").replace(/^Bearer\s+/i, "");
      if (!sameSecret(token, config.serviceToken)) {
        sendJson(response, 401, { error: { code: "UNAUTHORIZED", message: "Requester access token is invalid" } });
        return;
      }

      if (request.method === "GET" && url.pathname === "/wallet") {
        sendJson(response, 200, await walletView(config, startup, Boolean(activeWalletPassword)));
        return;
      }
      if (request.method === "POST" && url.pathname === "/wallet/faucet") {
        const body = await readBody(request);
        const result = await ensureWalletFunds(
          config,
          body.tokenTarget || config.autoFundTokenTarget,
          body.nativeTarget || config.autoFundNativeTarget,
        );
        sendJson(response, 200, result);
        return;
      }

      const platformResumeMatch = url.pathname.match(/^\/platform-workflows\/([^/]+)\/resume$/);
      if (request.method === "POST" && platformResumeMatch) {
        const workflowId = decodeURIComponent(platformResumeMatch[1]);
        const body = await readBody(request);
        const maxActions = Math.max(1, Math.min(Number(body.maxActions || 4), 100));
        sendJson(response, 200, await new PlatformClient(config).resume(workflowId, maxActions));
        return;
      }

      const workflowMatch = url.pathname.match(/^\/workflows\/([^/]+)\/(fund|run|status|summary)$/);
      if (workflowMatch) {
        const conversationId = decodeURIComponent(workflowMatch[1]);
        const action = workflowMatch[2];
        if (request.method === "POST" && action === "fund") {
          const body = await readBody(request);
          const coordinatorProfileId = String(body.coordinatorProfileId || "");
          const operation = fundingQueue.then(() => fundWorkflow(
            config,
            conversationId,
            activeWalletPassword,
            coordinatorProfileId,
          ));
          fundingQueue = operation.catch(() => undefined);
          sendJson(response, 200, await operation);
          return;
        }
        if (request.method === "POST" && action === "run") {
          const body = await readBody(request);
          const coordinatorProfileId = String(body.coordinatorProfileId || "");
          const existing = runs.get(conversationId);
          if (!existing || !isResumableRun(existing)) {
            const state = {
              conversationId,
              coordinatorProfileId,
              status: "RUNNING",
              cycle: 0,
              workflowStatus: null,
              lastAction: null,
              error: null,
              retryCount: 0,
              nextRetryAt: null,
              startedAt: now(),
              updatedAt: now(),
            };
            runs.set(conversationId, state);
          } else if (coordinatorProfileId) {
            existing.coordinatorProfileId = coordinatorProfileId;
          }
          await persistRuns();
          void driveWorkflow(conversationId);
          sendJson(response, 202, runs.get(conversationId));
          return;
        }
        if (request.method === "GET" && action === "status") {
          const coordinatorProfileId = url.searchParams.get("coordinatorProfileId") || runs.get(conversationId)?.coordinatorProfileId || "";
          const coordinator = new CoordinatorClient(config, coordinatorProfileId);
          const platform = new PlatformClient(config);
          const conversation = await coordinator.conversation(conversationId);
          const workflow = conversation.workflowId ? await platform.workflow(conversation.workflowId) : null;
          sendJson(response, 200, { run: runs.get(conversationId) || null, conversation, workflow });
          return;
        }
        if (request.method === "GET" && action === "summary") {
          const coordinatorProfileId = url.searchParams.get("coordinatorProfileId") || runs.get(conversationId)?.coordinatorProfileId || "";
          sendJson(response, 200, await new CoordinatorClient(config, coordinatorProfileId).summary(conversationId));
          return;
        }
      }

      sendJson(response, 404, { error: { code: "NOT_FOUND", message: "Requester endpoint was not found" } });
    } catch (error) {
      const status = Number(error?.statusCode || 500);
      sendJson(response, status, {
        error: {
          code: status >= 500 ? "REQUESTER_OPERATION_FAILED" : "REQUESTER_REQUEST_REJECTED",
          message: error instanceof Error ? error.message : String(error),
        },
      });
    }
  });

  server.listen(config.servicePort, config.serviceHost, () => {
    console.log(`local-requester-wallet-service listening on http://${config.serviceHost}:${config.servicePort}`);
    void ensureWalletFunds(config, config.autoFundTokenTarget, config.autoFundNativeTarget)
      .then(() => {
        startup.status = "READY";
        startup.checkedAt = now();
      })
      .catch((error) => {
        startup.status = "DEGRADED";
        startup.error = error instanceof Error ? error.message : String(error);
        startup.checkedAt = now();
      });
    for (const [conversationId, state] of runs) {
      if (isResumableRun(state)) void driveWorkflow(conversationId);
    }
  });
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : error);
  process.exitCode = 1;
});
