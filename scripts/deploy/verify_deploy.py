#!/usr/bin/env python3
"""Restart web container with rebuilt image and verify end-to-end public access."""
import os
import sys
sys.path.insert(0, ".")
from sshlib import run

# Public origin under test; keep it out of source control.
PUBLIC_HOST = os.environ.get("ECS_HOST", "<YOUR_SERVER_IP>")
HTTP_CODE_FMT = "'HTTP %{http_code} size=%{size_download}\\n'"

def step(name, cmd, timeout=120):
    print(f"\n=== {name} ===")
    out, err = run(cmd, timeout=timeout)
    if out.strip():
        print(out)
    if err.strip():
        print("[ERR]", err)
    return out, err

# 1. Current container state
step("Container status (before restart)", "cd /opt/agent-crosschain && docker compose ps --format 'table {{.Name}}\t{{.State}}\t{{.Status}}'")

# 2. Restart web with rebuilt image (pulls new image if tag changed)
step("Restart web service", "cd /opt/agent-crosschain && docker compose up -d web 2>&1 | tail -20")

# 3. Wait a moment for web to come up
step("Brief wait + web logs", "sleep 8 && cd /opt/agent-crosschain && docker compose logs --tail 15 web")

# 4. Verify public access through nginx proxy on port 80
step("Curl root via nginx (public IP)", f"curl -s -o /dev/null -w {HTTP_CODE_FMT} http://{PUBLIC_HOST}/")
step("Curl /api/docs via nginx", f"curl -s -o /dev/null -w {HTTP_CODE_FMT} http://{PUBLIC_HOST}/api/docs")
step("Curl /api/v1/health", f"curl -s -o /dev/null -w '%{{http_code}}\\n' http://{PUBLIC_HOST}/api/v1/health 2>/dev/null || curl -s -o /dev/null -w '%{{http_code}}\\n' http://{PUBLIC_HOST}/api/health 2>/dev/null || echo 'no health path tried'")
