#!/usr/bin/env python3
"""Launch web rebuild detached and poll its log (no self-matching pgrep)."""
from sshlib import run
import time

# launch detached
run('''
cd /opt/agent-crosschain/infra
setsid bash -c "docker compose build web > /tmp/rebuild_web.log 2>&1" < /dev/null &
echo launched
''')

for i in range(20):
    time.sleep(15)
    out, _ = run('''
echo "=== rebuild_web.log tail ==="
tail -6 /tmp/rebuild_web.log
echo "=== images ==="
docker images --format "{{.Repository}}:{{.Tag}}" | grep agent-crosschain-shop-web
''')
    print(f"--- poll {i+1} ---")
    print(out)
    if "Built" in out or "ERROR" in out or "failed" in out:
        # confirm web image present
        break
print("DONE polling")
