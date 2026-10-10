#!/usr/bin/env python3
"""Add NODE_OPTIONS to the chain-worker service in the REMOTE compose file via SFTP push."""
import sys
sys.path.insert(0, ".")
from sshlib import run, push

o, e = run("cat /opt/agent-crosschain/infra/docker-compose.yml")
content = o.replace("\r\n", "\n")  # normalize any CRLF

needle = "      CHAIN_WORKER_ID: ${CHAIN_WORKER_ID:-ccip-worker-1}\n"
repl = needle + "      NODE_OPTIONS: --experimental-strip-types\n"

if "NODE_OPTIONS" in content:
    print("NODE_OPTIONS already present; no edit needed")
elif needle in content:
    new = content.replace(needle, repl, 1)
    local = "docker-compose.tmp.yml"
    with open(local, "w") as f:
        f.write(new)
    push(local, "/opt/agent-crosschain/infra/docker-compose.yml")
    print("PUSHED edited compose with NODE_OPTIONS")
else:
    print("NEEDLE NOT FOUND. chain-worker CHAIN_WORKER_ID lines:")
    for ln in content.splitlines():
        if "CHAIN_WORKER_ID" in ln or "NODE_OPTIONS" in ln:
            print("  ", repr(ln))

o2, e2 = run("grep -n NODE_OPTIONS /opt/agent-crosschain/infra/docker-compose.yml")
print("verify grep:", o2.strip() or "(none)")
