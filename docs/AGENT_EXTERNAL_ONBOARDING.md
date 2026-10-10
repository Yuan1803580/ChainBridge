# External Agent Onboarding (Plan B — pull both images)

> **Scope:** how an operator on **another machine** brings their agent into the
> platform by pulling two prebuilt images (Signer Sidecar + Agent Runner) and
> running them as a co-located pair. The signer holds the agent's identity +
> wallet private keys and is reached **only via loopback** by the co-located
> runner; the platform only ever reaches the agent's **runner endpoint**.
>
> **Testnet only.** Use testnet wallets. Never put a production wallet or real
> funds through this flow.

> **Register as `executor_type="http"` (not the default `llm`).** The platform now
> keeps the external runner's own public `endpoint` and skips managed runtime
> provisioning when `executor_type="http"`. When running the Plan B bundle, onboard
> with `EXECUTOR_TYPE=http` and `EXTERNAL_ENDPOINT=<public runner /invoke URL>` (see
> `tmp/onboard_self.py`). The full HTTP contract is in `docs/EXTERNAL_RUNNER_CONTRACT.md`.

---

## 1. Architecture premise (read this first)

```
   PLATFORM (ECS)                     EXTERNAL OPERATOR MACHINE
 +------------------+               +-------------------------------+
 | API + chain-      |   dispatch    | agent-runner  (public :8010)  |
 | worker           | <-----------  |      |                        |
 |                  |   /invoke     |      | (network_mode:          |
 |                  |               |      |   service:signer)       |
 |  verifies        |               |      v                        |
 |  signatures      |               | signer sidecar :8020          |
 |                  |   (NEVER      |   (127.0.0.1 ONLY, loopback)  |
 |  reaches signer) |    reached)   +-------------------------------+
 +------------------+
```

- The **signer** holds the agent's identity + wallet private keys and signs
  locally. It is reachable **only via loopback** (`127.0.0.1:8020`) by the
  co-located runner — never published to the network.
- The **runner** executes the agent's capability and is **publicly reachable**
  so the platform can dispatch tasks to it.
- The platform **never** contacts the external signer and **cannot** extract the
  agent's private key. This is the security model the whole system relies on.

> **Critical constraint:** the runner must share the signer's network namespace
> (`network_mode: "service:signer"`) so it can use `http://127.0.0.1:8020`. This
> is enforced by `services/agent-runner/app/managed.py:143-145`. `docker-compose.agent.yml`
> wires this for you.

---

## 2. Prerequisites

- Docker (20.10+) on the operator machine.
- The platform must be in `managed-testnet` self-service mode with the fixed
  CCIP route **ACTIVE + READY** and `RemoteWorkBoard` deployed
  (`GET /api/v1/chain-routes`).
- The operator has an `agentKey`, and the platform's `onboarding-config`
  (`GET /api/onboarding-config`) shows `selfService.enabled = true`.

---

## 3. One-command start (pull the published images)

The two images are published to GHCR:

- `ghcr.io/yuan1803580/agent-signer:latest`
- `ghcr.io/yuan1803580/agent-runner:latest`

> **Package visibility (read this):** the two packages are currently **private**.
> To let an external machine pull them **without** `docker login` (true one-click),
> flip each package to **Public** once in the GitHub web UI:
> _GitHub → your avatar → Your packages → `agent-signer` (and `agent-runner`) →
> Package settings → Change visibility → Public_.
> Until then, the operator must run `docker login ghcr.io -u yuan1803580 -p <token>`
> (a token with `read:packages`) once before `docker compose up`.

```bash
# 1. Prepare env + signer config (one time)
cp agent.env.example agent.env
mkdir -p signer-config
cp services/agent-signer/config.example.json signer-config/signer-config.json

# 2. (Recommended) pin stable secrets so they survive restarts
python -c "import secrets; print('SIDECAR_PROVISIONING_TOKEN='+secrets.token_hex(24))"
python -c "from cryptography.fernet import Fernet; print('SIDECAR_STORAGE_KEY='+Fernet.generate_key().decode())"
#   -> paste the two lines into agent.env

# 3. Pull + launch the signer + runner pair
docker compose -f docker-compose.agent.yml --env-file agent.env up -d

# 4. Confirm the signer is alive (loopback only)
docker compose -f docker-compose.agent.yml exec signer wget -qO- http://127.0.0.1:8020/health
```

What the compose does for you:
- pulls both images;
- keeps the signer on loopback (`127.0.0.1:8020`, never published);
- shares the signer's network namespace with the runner so loopback signing works;
- publishes the **runner's** port `8010` (via the shared netns) so the platform
  can dispatch to it;
- the signer entrypoint auto-generates `SIDECAR_PROVISIONING_TOKEN` and
  `SIDECAR_STORAGE_KEY` when they are blank, and runs `alembic upgrade head`
  (because `SIDECAR_RUN_MIGRATIONS=true`);
- `/v1/provision` **rewrites** `signer-config/signer-config.json` to append the
  provisioned agent — so that mount MUST be read-write (it is).

---

## 4. Build & push the images (maintainer)

> The local dev sandbox cannot reach Docker Hub, so base images
> (`python:3.x-slim`) cannot be pulled there. Build on a host that has the base
> images and registry push credentials — e.g. the ECS server, which already
> caches them.
>
> **The images are already published** at `ghcr.io/yuan1803580/agent-signer:latest`
> and `ghcr.io/yuan1803580/agent-runner:latest`. Rebuild + push only when you
> change the signer/runner source.

```bash
# On the build host (repo root):
docker build -f services/agent-signer/Dockerfile -t ghcr.io/yuan1803580/agent-signer:latest .
(cd services/agent-runner && docker build -f Dockerfile -t ghcr.io/yuan1803580/agent-runner:latest .)

# Push (requires a GHCR token with write to yuan1803580)
echo "$GHCR_TOKEN" | docker login ghcr.io -u yuan1803580 --password-stdin
docker push ghcr.io/yuan1803580/agent-signer:latest
docker push ghcr.io/yuan1803580/agent-runner:latest
```

Notes:
- The signer image bakes in `docker-entrypoint.sh` (auto-secret generation +
  optional migrations) and keeps the managed deploy's behavior unchanged (the
  managed compose overrides `command` and supplies the secrets via env).
- The runner image bakes in `alembic.ini` + `alembic/` so it is self-contained
  for external operators (the managed deploy still mounts them from the host,
  overriding the in-image copies).
- Both services default to **sqlite** (`*_DATABASE_URL`), so no Postgres is
  needed in the external bundle — just the `/app/data` volumes.

---

## 5. Register the agent (onboard)

Run the self-service onboard script **on the operator machine** (where the
bundle is running). Because the platform advertises `sidecarUrl =
http://127.0.0.1:8020`, and that loopback address resolves to the operator's
**own** signer on their machine, the existing script works unchanged:

```bash
export PLATFORM_API_URL=https://<YOUR_SERVER_IP>/api
export SIDECAR_PROVISIONING_TOKEN=<same token the signer container uses>
# PowerShell (recommended on Windows operator machines):
$source = Invoke-RestMethod "https://<YOUR_SERVER_IP>/skills/agent-market-onboarding/onboard-agent.ps1"
& ([ScriptBlock]::Create($source)) -WebOrigin "https://<YOUR_SERVER_IP>" `
  -OwnerName "..." -OwnerEmail "..." -AgentName "..." -AgentKey "<unique>" -Roles "provider"
# or the Python equivalent:
python tmp/onboard_self.py
```

The script:
1. reads `onboarding-config` and confirms the chain route is READY;
2. calls the **local** signer `/v1/provision` (loopback) to mint identity +
   wallet, which also appends the agent to `signer-config.json`;
3. requests an invite, registers, signs both identities via the local signer;
4. verifies, then (provider) runs `/complete` technical checks or (requester)
   just activates via `/status`;
5. the runner sends heartbeats to flip `availability=ONLINE` and become matchable.

The `signerUrl` recorded with the platform is `http://127.0.0.1:8020` — the
platform never contacts it; the **co-located runner** uses it locally.

---

## 6. Security checklist

- [ ] `SIDECAR_PROVISIONING_TOKEN` is **never** sent to the platform; it only
      gates the local `/v1/provision` endpoint.
- [ ] Port **8020 is not published** — the signer is loopback-only.
- [ ] `signer-config` is mounted **read-write** so provisioning can append agents.
- [ ] The runner endpoint (`RUNNER_PORT`, default 8010) uses **HTTPS** (reverse
      proxy / TLS) before the platform dispatches to it.
- [ ] `DEEPSEEK_API_KEY` lives in the **runner** env, never on the platform.
- [ ] Testnet wallet only; no real funds.
- [ ] If you must expose the signer (Plan A / remote signing), use the
      `expose` profile + `Caddyfile` with **mTLS** and keep `/v1/provision`
      blocked. Never expose the signer over plain HTTP.

---

## 7. Note for experiments / test data

The settlement metrics you care about — latency, p95, CCIP transport share, cost
(ETH/run), success rate, concurrency, and the on-chain event tables
(`settlements`, `settlement_chain_events`, `relayer_tasks`, `orders`) — are
produced by the **chain-worker + contracts**, which are **identical** regardless
of whether the agent runs on the platform or externally. Switching Plan B in/out
does **not** change or delete that data.

When you mix topologies in one experiment batch, tag each run with a `topology`
label (e.g. `single-host` vs `plan-b-external`) so the cohorts stay separable in
analysis.
