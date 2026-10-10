# 跨链 Agent 市场 — 威胁模型与安全机制分析

> 本文基于仓库实际代码（`contracts/`、`services/agent-signer`、`services/chain-worker`、`apps/api`、`services/agent-runner`）整理，覆盖已部署的 `ccip_direct` 模式（Sepolia 源链 → Arbitrum Sepolia 目标链）。
>
> 分析时间：2026-08-09。结论均为代码事实，非泛泛而谈。

## 0. 系统边界与信任根

五大组件：escrow 合约 / Signer 侧车（per-agent 签名服务）/ Chain Worker（链上事件监听 + relayer 代付 CCIP native fee）/ 平台 API / Agent Runner（托管 agent 运行时）。

**关键信任根（一旦失陷，系统安全模型即崩塌）：**

| 信任根 | 形态 | 失陷后果 |
|---|---|---|
| `platformSigner` | escrow 合约 `immutable` 地址，唯一可 `release`/`refund`/`confirmCrossChainRelease` 的实体 | 任意释放/退款，直接卷走托管资金 |
| `RELAYER_PRIVATE_KEY`（Chain Worker env） | **即 `platformSigner` 私钥**（relayer 调 `releaseRealCrossChain`/`confirmCrossChainRelease` 必须满足 `msg.sender==platformSigner`） | 同上——relayer 密钥 = 平台信任根，单点 |
| `SIDECAR_PROVISIONING_TOKEN` + per-agent `signerCredentialRef` | Signer 侧车账号凭证 | 冒领/伪造 agent 身份与签名 |
| `WORKER_INTENT_HMAC_SECRET` | Worker↔Signer/Runner 交易意图鉴权 | 伪造签名请求 |
| `LISTENER_HMAC_KEYS` | Worker↔API 内部调用鉴权 | 伪造链上事件回执 |
| `RUNNER_CONTROL_HMAC_SECRET` / `AGENT_RUNNER_API_TOKEN` | Runner 控制面 / invoke 鉴权 | 越权入驻/调用 agent |
| CCIP Router（Chainlink） | 跨链消息传递信任 | 源码链/发送方伪造（已白名单缓解） |

---

## 1. 合约层威胁模型（链上，`contracts/src`）

| 威胁 | 编号 | 现有安全机制 | 残留风险 |
|---|---|---|---|
| 未授权释放/退款 | T-A1 | `release`/`refund`/`releaseRealCrossChain` 均校验 `msg.sender==payer \|\| platformSigner`；状态码机 `Locked` 前置校验 | `platformSigner` 单点（见 T-X1） |
| 重入攻击 | T-A2 | 状态（`status`）在外部 `safeTransfer` 前更新；SafeERC20；重入会命中 `InvalidStatus` | 无显式 `nonReentrant`；依赖 SafeERC20，恶意 token 回调风险极低 |
| 跨链消息重放 | T-A3 | Sender 侧 `sentMessages[messageId]`；Receiver 侧 `processedMessages[messageId]` 校验；`confirmCrossChainRelease` 比对 `releaseMessageIds` | 已充分缓解 |
| 错误链/代币/收款人 | T-A4 | Sender `_validateRoute`：`allowedDestinationChainSelector`/`allowedReceiver`/`allowedToken`；Receiver 校验 `allowedSourceChainSelector`/`allowedRemoteSender`/`allowedToken` | 已缓解 |
| 报价篡改 / 结算条件替换 | T-A5 | `createAndLockWithQuote`：`termsHash` 绑定（keccak(orderId,baseTermsHash,quoteHash)）、`quoteDeadline` 过期、`minSettlementAmount` 下限 | 已缓解 |
| settlementId 碰撞重放 | T-A6 | `createAndLock` 前置 `status!=None → SettlementAlreadyExists` | 已缓解 |
| CCIP 消息伪造 | T-A7 | `CCIPReceiver` 仅 CCIP Router 可回调；`sourceChainSelector`+`remoteSender` 白名单 | 依赖 Chainlink Router 信任 |
| native fee 操纵/退款失败 | T-A8 | `quoteFee` + `msg.value` 校验 + `NativeFeeRefundFailed` 回退 | 已缓解 |
| 释放后目标链长时间未确认（活性） | T-A9 | release 立即退 payer 款，payee 在目标链收；卡 `ReleasePending` 需 `platformSigner` confirm | 失败需运维/人工干预；非资金安全问题 |
| 源链 reorg 导致事件错乱 | T-A10 | 由 Chain Worker `verifyCanonicalHistory` + `reorgDepth` 回退扫描处理（见 T-C3） | 依赖 worker 正确运行 |

**结算双层门禁（审核 V2）：** `TaskEscrow`/`ReviewManagerV2` 在付款前同时校验「API 已完成 `SYNC_REVIEW_V2`」与链上 `isSettlementAllowed(attemptId)`；挑战期内 `isSettlementAllowed=false`，`block.timestamp` 驱动时间锁，API 无法提前关闭。

---

## 2. Signer 侧车层威胁模型（`services/agent-signer`）

| 威胁 | 编号 | 现有安全机制 | 残留风险 |
|---|---|---|---|
| 侧车 API 未授权访问 | T-B1 | 每 agent 凭证 `sha256(token)` 比对，`secrets.compare_digest`（时序安全）；`scopes` 最小化（identity:sign/result:sign/wallet:sign/status:write） | 凭证泄露等同该 agent 被控 |
| 越权签名（任意消息/交易） | T-B2 | identity 签名仅允许 `onboarding/heartbeat/DID auth` 前缀；runtime-result 字段白名单 + DID/agentId 绑定；transaction 签名强制 selector=`submitResult` 且 calldata 与已签 canonical result 严格匹配 | 已深度缓解 |
| Worker 伪造签名请求 | T-B3 | `verify_worker_signature`：`WORKER_INTENT_HMAC_SECRET` 对 `canonical_json(intent)` 做 HMAC | secret 泄露可伪造 |
| 私钥泄露 | T-B4 | 文件权限 `0o600`/`0o700`；`Fernet(SIDECAR_STORAGE_KEY)` 加密 raw tx；路径穿越防护（`runtimeId` 必须落在 `SIDECAR_KEY_DIR` 内）；原子写 | env 明文 `SIDECAR_STORAGE_KEY` 风险 |
| nonce 重放/双花 | T-B5 | `WalletNonce`/`NonceReservation` 预留；`requestId` 绑定单一 intent（冲突 409） | 已缓解 |
| 费用耗尽（gas griefing） | T-B6 | `maxGasLimit`/`maxFeePerGas`/`maxPriorityFeePerGas`/`maxTotalFeeWei` 上限 | 已缓解 |
| 入驻（provisioning）越权 | T-B7 | 仅 loopback 可调用 + `SIDECAR_PROVISIONING_TOKEN` 比对；`runtimeId`/`DID`/`agentSlug` 唯一性；路径穿越防护 | 生产需保证 token 强度 |
| 交易意图过期/滞后 | T-B8 | `expires_at` 与 `observed_at` 120s 窗口 | 已缓解 |
| 替换/取消攻击 | T-B9 | replace 必须 ≥12.5% fee bump；cancel 必须零值自交易 | 已缓解 |

---

## 3. Chain Worker 层威胁模型（`services/chain-worker`）

| 威胁 | 编号 | 现有安全机制 | 残留风险 |
|---|---|---|---|
| 伪造内部 API 调用 | T-C1 | `require_listener_auth`：HMAC(method+timestamp+nonce+path+body) + 时间窗 + 每 listener 密钥 + nonce 去重（409） | secret 泄露可伪造 |
| 链上事件重放/重复结算 | T-C2 | `processed_events` PK + Sender/Receiver 合约 `sentMessages`/`processedMessages`；200 `duplicate` vs 409 `conflict` 严格区分 | 已缓解 |
| Reorg / 区块重组 | T-C3 | `verifyCanonicalHistory` 比对区块哈希，发现 orphan 调 `api.orphan` 并标记；`reorgDepth` 游标回退 | 依赖 worker 运行 |
| ABI 静默丢日志 | T-C4 | `rawTopicProbe` + `assertDecodeCoverage`：strict 解码丢日志则**抛错不前进游标**（fail-loud），精度远超普通实现 | 已良好缓解 |
| Relayer 私钥泄露 | T-C5 | `RELAYER_PRIVATE_KEY`（env）；文档/代码注释明确生产应 `kms` 适配 | **env 明文即 `platformSigner`**，单点（见 T-X1） |
| 重复释放 | T-C6 | 租约 `lease` + `saveSubmission` 去重；dropped 检测重发；合约状态机 + `confirmCrossChainRelease` messageId 校验 | 已缓解 |
| RPC 篡改 / MITM | T-C7 | 依赖可信 RPC + TLS | 无 RPC 响应签名/多节点一致性校验 |

> 评测结论（memory）：20/20 成功、零重复释放，证明幂等与去重机制有效。

---

## 4. 平台 API 层威胁模型（`apps/api`）

| 威胁 | 编号 | 现有安全机制 | 残留风险 |
|---|---|---|---|
| 未授权访问 | T-D1 | JWT（HS256，aud/iss，过期）+ protocol worker token（sha256 比对）+ `require_role` | HS256 依赖 `app_secret` 强度 |
| 监听器请求重放 | T-D2 | `IntegrationRequestNonce` 表 + `listener_signature_window_seconds` 时间窗 | 已缓解 |
| 结算事件与链上数据不一致 | T-D3 | 409 = 真实冲突（同 eventId 不同链上数据）→ 立即死信+告警；200 `duplicate` = 良性重放 | 已缓解 |
| 任务 offer 伪造 | T-D4 | `coordinator_signature` HMAC（runner 侧验证） | 依赖 `AGENT_RUNNER_API_TOKEN` 强度 |

---

## 5. Agent Runner 层威胁模型（`services/agent-runner`）

| 威胁 | 编号 | 现有安全机制 | 残留风险 |
|---|---|---|---|
| 托管控制面越权 | T-E1 | `RUNNER_CONTROL_HMAC_SECRET` + `X-Runner-Timestamp/Nonce/Signature` + 60s 窗口 + nonce 去重（409） | secret 泄露可越权 |
| Runner 本地持有 agent 私钥 | T-E2 | **managed 模式禁止加载本地钱包密钥**；私钥由 Sidecar 托管；启动检测到 `AGENT_HOME_WALLET_PRIVATE_KEYS_JSON` 即拒绝启动，注入后即从父进程环境删除 | 本地 JSON 仅测试用 |
| Agent 签名任意合约/方法 | T-E3 | `allowedContracts` + **白名单 artifact 字节码哈希匹配**（`eth_getCode` 比对 `immutableReferences`，屏蔽不可变段）+ selector 限制 | 已深度缓解 |
| attemptId 替换 / calldata 篡改 | T-E4 | `attemptId`→bytes32 绑定（`_opaque_id_to_bytes32`）+ calldata 必须包含 attemptId | 已缓解 |
| 费用/价值越界 | T-E5 | `maxValueWei`/`maxGas`/`maxGasPriceWei` 上限 | 已缓解 |
| 请求重放 | T-E6 | `request_nonce` 去重（409）+ 持久化 nonce store | 已缓解 |
| 心跳 / DID 冒用 | T-E7 | `heartbeat-challenge` ES256K 签名 | 已缓解 |
| 任务 offer 重放/替换 | T-E8 | `offerId` 绑定 + 过期检查 + `coordinator_signature` | 已缓解 |

---

## 6. 跨层 / 系统性威胁（最高优先级）

| 威胁 | 编号 | 现状 | 建议 |
|---|---|---|---|
| 平台信任根单点（`platformSigner` == `RELAYER_PRIVATE_KEY`） | T-X1 | **单一私钥 env 托管**，可无条件释放/退款 | 多签（≥m/n）+ 时延 timelock；HSM 托管；操作审计 |
| Reviewer/Arbitrator 白名单中心化 | T-X2 | 文档自陈：生产应改多签 + 最小权限治理 | 接入治理多签 |
| VRF 抽样真实性 | T-X3 | 本地 `MOCK` 模式，`REVIEW_RANDOMNESS_MODE=VRF` 尚未接真实 Chainlink VRF 订阅 | 生产接测试网 VRF 订阅 + 回执验收 |
| 第三方安全审计缺失 | T-X4 | 文档自陈：未做真实资产审计 | 上线前第三方审计 |
| 密钥管理散落 env/.env | T-X5 | relayer / listener HMAC / worker HMAC / signer storage / platformSigner 均 env | 统一 KMS/Secret Manager/HSM |
| 测试私钥误用 | T-X6 | 文档明确本地 JSON 私钥禁上生产/测试网 | CI 卡点拦截 |
| 传输安全 | T-X7 | signer/runner 仅 loopback http；平台 URL 生产强制 HTTPS | 内部链路补 mTLS/TLS |
| RPC 单点 | T-C7 | 单一可信 RPC | 多节点 + 响应一致性校验 |

---

## 7. 结论

系统已具备**相当成熟的纵深防御**：

- **链上**：最小权限（platformSigner/payer 双控）+ 状态码机 + CCIP 白名单（源链/发送方/目标链/接收方/代币）+ 幂等去重 + 双层结算门禁（审核 V2 + 挑战时间锁 + 阈值多签）。
- **侧车**：per-agent 凭证 + scope 最小化 + 交易意图 HMAC + 合约字节码白名单 + selector 锁定 + 费用上限 + nonce 预留 + 文件权限/加密。
- **Worker**：监听器 HMAC + nonce 时间窗 + 幂等（200/409 区分）+ reorg 检测 + ABI 失配 fail-loud。
- **API**：JWT + 监听器 HMAC nonce + 角色校验 + 数据完整性冲突死信。
- **Runner**：控制面 HMAC + managed 模式禁止本地私钥 + artifact 字节码匹配 + attemptId 绑定 + 费用上限 + nonce 去重。

**主要残留风险集中在四处**：① `platformSigner`/`RELAYER_PRIVATE_KEY` 单一信任根（最关键）；② 密钥散落 env，缺统一 KMS/HSM；③ VRF 仍 MOCK；④ 未做真实资产第三方审计。前两项应作为上线前最高优先级加固项。
