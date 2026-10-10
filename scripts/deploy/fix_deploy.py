#!/usr/bin/env python3
"""Server-side fix: repair LISTENER_HMAC_KEYS in .env files and add DEV_IDENTITY_SEED
passthrough to the agent-runner compose service. Run on the ECS via python3."""
import os, secrets, subprocess, sys

# Value of CHAIN_WORKER_HMAC_SECRET (worker signs with this; API must verify with the same)
HMAC_SECRET = os.environ.get("CHAIN_WORKER_HMAC_SECRET")
if not HMAC_SECRET:
    sys.exit(
        "ERROR: CHAIN_WORKER_HMAC_SECRET is not set. Export it from your secrets "
        "manager before running this script. The previously hardcoded value has "
        "been removed for security."
    )
ENV_FILES = ["/opt/agent-crosschain/infra/.env", "/opt/agent-crosschain/.env"]
COMPOSE = "/opt/agent-crosschain/infra/docker-compose.yml"

def fix_env(path):
    if not os.path.exists(path):
        print("MISSING", path); return
    with open(path, "r", encoding="utf-8") as fh:
        raw = fh.read()
    # strip trailing \r (CRLF)
    lines = [ln.rstrip("\r") for ln in raw.split("\n")]
    out, seen_listener, seen_seed = [], False, False
    for ln in lines:
        if ln.startswith("LISTENER_HMAC_KEYS="):
            out.append(f"LISTENER_HMAC_KEYS=worker1:{HMAC_SECRET}")
            seen_listener = True
        elif ln.startswith("DEV_IDENTITY_SEED="):
            out.append(ln); seen_seed = True
        else:
            out.append(ln)
    if not seen_listener:
        out.append(f"LISTENER_HMAC_KEYS=worker1:{HMAC_SECRET}")
    if not seen_seed:
        out.append(f"DEV_IDENTITY_SEED={secrets.token_hex(32)}")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out).rstrip("\n") + "\n")
    print("FIXED_ENV", path)

for f in ENV_FILES:
    fix_env(f)

# Compose edit: add DEV_IDENTITY_SEED passthrough to agent-runner environment
with open(COMPOSE, "r", encoding="utf-8") as fh:
    content = fh.read()

old = """    environment:
      APP_ENV: development
    ports:
      - "8010:8010"
"""
new = """    environment:
      APP_ENV: development
      DEV_IDENTITY_SEED: ${DEV_IDENTITY_SEED:-agent-market-dev-identity-seed}
    ports:
      - "8010:8010"
"""
if "DEV_IDENTITY_SEED" in content:
    print("COMPOSE already has DEV_IDENTITY_SEED passthrough; skipping")
elif old in content:
    content = content.replace(old, new)
    with open(COMPOSE, "w", encoding="utf-8") as fh:
        fh.write(content)
    print("COMPOSE EDITED (agent-runner DEV_IDENTITY_SEED added)")
else:
    print("COMPOSE PATTERN NOT FOUND - inspect manually")

# sanity: confirm the new env value made it into the infra .env
with open(ENV_FILES[0]) as fh:
    for ln in fh:
        if ln.startswith("LISTENER_HMAC_KEYS="):
            v = ln.strip().split(":", 1)[1]
            print(f"SANITY infra LISTENER_HMAC_KEYS secret_len={len(v)} empty={v==''}")
