#!/usr/bin/env python3
"""Production readiness gate for the cross-chain agent marketplace.

Runs a fixed battery of checks against the live deployment and exits non-zero if any
BLOCKING check fails. Every check here exists because its absence already caused a real
outage during bring-up - this is a regression net, not a checklist for show.

Usage (from scripts/deploy/):
    python verify_production.py            # full run
    python verify_production.py --quick    # skip the on-chain RPC probes

Exit codes: 0 = all blocking checks passed, 1 = at least one blocking check failed.
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, ".")
from sshlib import run  # noqa: E402

COMPOSE = "docker compose -p agent-crosschain-shop -f /opt/agent-crosschain/infra/docker-compose.yml"
ENV_FILES = ["/opt/agent-crosschain/.env", "/opt/agent-crosschain/infra/.env"]
API_CONTAINER = "agent-crosschain-shop-api-1"
WORKER_CONTAINER = "agent-crosschain-shop-chain-worker-1"
PUBLIC = os.environ.get("ECS_PUBLIC_URL", "http://<YOUR_SERVER_IP>")

# Relayer must stay funded or every release silently queues forever.
MIN_RELAYER_ETH = 0.05

RESULTS = []


def record(name, ok, detail, blocking=True):
    RESULTS.append({"name": name, "ok": ok, "detail": detail, "blocking": blocking})
    mark = "PASS" if ok else ("FAIL" if blocking else "WARN")
    print(f"[{mark}] {name}: {detail}", flush=True)
    return ok


def sh(cmd, timeout=90):
    out, err = run(cmd, timeout=timeout)
    return (out or "").strip(), (err or "").strip()


def env_value(key):
    """Read a key from the server env files. Returns the first non-empty value."""
    for path in ENV_FILES:
        out, _ = sh(f"grep -m1 '^{key}=' {path} 2>/dev/null | cut -d= -f2-")
        if out:
            return out
    return ""


def check_containers():
    out, _ = sh(f"{COMPOSE} ps --format '{{{{.Name}}}} {{{{.State}}}}'")
    states = dict(
        line.split(None, 1) for line in out.splitlines() if len(line.split(None, 1)) == 2
    )
    required = ["api", "web", "postgres", "redis", "chain-worker"]
    missing = []
    for svc in required:
        match = [n for n in states if svc in n]
        if not match or "running" not in states[match[0]].lower():
            missing.append(svc)
    record(
        "containers running",
        not missing,
        f"{len(states)} containers up" if not missing else f"not running: {missing}",
    )


def check_http():
    for path, label in [("/", "web via nginx"), ("/api/v1/health", "api health")]:
        out, _ = sh(f"curl -s -o /dev/null -m 15 -w '%{{http_code}}' {PUBLIC}{path}")
        record(f"http {label}", out == "200", f"HTTP {out or 'no response'}")


def check_worker_health():
    # The chain-worker image ships neither wget nor curl - only the node runtime.
    probe = 'fetch("http://127.0.0.1:9090/health").then(r=>r.text()).then(console.log)'
    out, _ = sh(f"docker exec {WORKER_CONTAINER} node -e '{probe}' 2>/dev/null")
    body = re.search(r"\{.*\}", out or "", re.S)
    if not body:
        record("chain-worker /health", False, f"no response from health endpoint: {(out or '')[:120]}")
        return
    try:
        health = json.loads(body.group(0))
    except json.JSONDecodeError:
        record("chain-worker /health", False, f"non-JSON body: {out[:120]}")
        return

    record("chain-worker /health", health.get("status") in ("ok", "starting"),
           f"status={health.get('status')} lastCycleAt={health.get('lastCycleAt')}")
    # `lastError` is historical; `failingStepNames` is what is broken right now.
    failing = health.get("failingStepNames") or []
    record("no loop step currently failing", not failing,
           f"failing={failing}" if failing else f"all steps healthy (last historical error: {health.get('lastError') or 'none'})")
    # The two silent killers: strict-decode ABI drops and duplicate-eventId conflicts.
    # A worker image predating these counters reports them as absent, which reads as 0.
    abi_drops = health.get("abiMismatchCount", 0) or 0
    conflicts = health.get("duplicateConflictCount", 0) or 0
    record("no ABI mismatch drops", abi_drops == 0,
           f"abiMismatchCount={abi_drops} {health.get('lastAbiMismatchDetail') or ''}")
    record("no duplicate-event conflicts", conflicts == 0,
           f"duplicateConflictCount={conflicts} {health.get('lastDuplicateConflictDetail') or ''}")
    record("no dead-letter events", health.get("deadLetters", 0) == 0,
           f"deadLetters={health.get('deadLetters')} pendingEvents={health.get('pendingEvents')}")
    record("scanner cursors set", bool(health.get("sourceNextBlock")) and bool(health.get("destinationNextBlock")),
           f"source={health.get('sourceNextBlock')} destination={health.get('destinationNextBlock')}")

    balance_wei = int(health.get("relayerBalanceWei") or 0)
    record("relayer funded", balance_wei / 1e18 >= MIN_RELAYER_ETH,
           f"{balance_wei / 1e18:.6f} ETH (min {MIN_RELAYER_ETH})")


def check_env_invariants():
    worker_secret = env_value("CHAIN_WORKER_HMAC_SECRET")
    listener_keys = env_value("LISTENER_HMAC_KEYS")
    # API aborts at boot in production unless the listener key matches the worker secret.
    ok = bool(worker_secret) and listener_keys.strip() == f"worker1:{worker_secret}"
    record("LISTENER_HMAC_KEYS matches worker secret", ok,
           "matched" if ok else f"LISTENER_HMAC_KEYS='{listener_keys[:24]}...' does not equal 'worker1:<CHAIN_WORKER_HMAC_SECRET>'")

    for key in ("SOURCE_START_BLOCK", "DESTINATION_START_BLOCK"):
        value = env_value(key)
        numeric = value.isdigit() and int(value) > 0
        # START_BLOCK=0 backfills millions of blocks and never reaches head.
        record(f"{key} is a real deployment block", numeric, f"{key}={value or '<unset>'}")

    for key in ("PLATFORM_ADMIN_DID", "PLATFORM_ADMIN_PUBLIC_KEY"):
        record(f"{key} present", bool(env_value(key)), "set" if env_value(key) else "MISSING - api will Exit(3)")

    mode = env_value("SETTLEMENT_MODE")
    record("SETTLEMENT_MODE", mode == "ccip_direct", f"SETTLEMENT_MODE={mode or '<unset>'}")


def check_database():
    def psql(sql):
        out, _ = sh(
            "docker exec agent-crosschain-shop-postgres-1 psql -U agent_market -d agent_market "
            f"-tAc \"{sql}\""
        )
        return out

    # `released` is the SUCCESS terminal state for a settlement (the order flips to `completed`
    # in the same transaction, main.py:1882-1884). Counting it as stalled produces false alarms.
    terminal = "'released','completed','failed','refunded','cancelled'"
    stuck = psql(
        f"SELECT count(*) FROM settlements WHERE status NOT IN ({terminal}) "
        "AND status <> 'lock_pending' AND created_at < now() - interval '45 minutes';"
    )
    record("no settlements stalled mid-flight >45min", stuck == "0",
           f"{stuck or '?'} stalled settlement(s)")

    # A settlement parked in `lock_pending` means the requester never broadcast createAndLock.
    # That is an abandoned order, not a platform fault - informational only.
    abandoned = psql(
        "SELECT count(*) FROM settlements WHERE status = 'lock_pending' "
        "AND created_at < now() - interval '45 minutes';"
    )
    record("no abandoned funding attempts", abandoned == "0",
           f"{abandoned or '?'} settlement(s) never funded by the requester", blocking=False)

    failed_tasks = psql("SELECT count(*) FROM relayer_tasks WHERE status = 'failed';")
    record("no failed relayer tasks", failed_tasks == "0", f"{failed_tasks or '?'} failed task(s)", blocking=False)

    orphaned = psql("SELECT count(*) FROM settlement_chain_events WHERE canonical_status = 'orphaned';")
    record("no orphaned chain events", orphaned == "0", f"{orphaned or '?'} orphaned event(s)", blocking=False)


def check_contracts():
    script = """
import json,os,urllib.request
def rpc(url,m,p):
    req=urllib.request.Request(url,data=json.dumps({"jsonrpc":"2.0","id":1,"method":m,"params":p}).encode(),
        headers={"Content-Type":"application/json","User-Agent":"Mozilla/5.0"})
    return json.load(urllib.request.urlopen(req,timeout=30)).get("result")
SRC="https://ethereum-sepolia-rpc.publicnode.com"
DST="https://sepolia-rollup.arbitrum.io/rpc"
out={}
for name,url,addr in [
  ("escrow",SRC,"0x192068e261b6eb553f58a084027cc38c40ef0d17"),
  ("ccip_sender",SRC,"0x2f1c7064088ed4dc1e2f1fd1d2838091c8e86da6"),
  ("ccip_receiver",DST,"0x409c31b165aa2f12d47c5eb60fa59f73d5277778")]:
    code=rpc(url,"eth_getCode",[addr,"latest"]) or "0x"
    out[name]=len(code)
print(json.dumps(out))
"""
    out, _ = sh(f"docker exec -i {API_CONTAINER} python3 - <<'PYEOF'\n{script}\nPYEOF", timeout=120)
    payload = re.search(r"\{.*\}", out or "")
    if not payload:
        record("contracts deployed", False, f"probe failed: {(out or '')[:160]}")
        return
    sizes = json.loads(payload.group(0))
    for name, size in sizes.items():
        record(f"contract {name} has bytecode", size > 2, f"code length {size}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true", help="skip on-chain RPC probes")
    args = parser.parse_args()

    print("=== production readiness verification ===\n", flush=True)
    check_containers()
    check_http()
    check_worker_health()
    check_env_invariants()
    check_database()
    if not args.quick:
        check_contracts()

    blocking_failures = [r for r in RESULTS if not r["ok"] and r["blocking"]]
    warnings = [r for r in RESULTS if not r["ok"] and not r["blocking"]]
    passed = [r for r in RESULTS if r["ok"]]

    print(f"\n=== summary: {len(passed)} passed, {len(warnings)} warning(s), "
          f"{len(blocking_failures)} blocking failure(s) ===")
    for item in blocking_failures:
        print(f"  BLOCKING  {item['name']}: {item['detail']}")
    for item in warnings:
        print(f"  warning   {item['name']}: {item['detail']}")
    return 1 if blocking_failures else 0


if __name__ == "__main__":
    sys.exit(main())
