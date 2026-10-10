#!/usr/bin/env python3
"""Full compose state + which services are up."""
from sshlib import run

def section(t):
    print("\n" + "=" * 70); print(t); print("=" * 70)

def main():
    section("docker ps -a (all containers)")
    out, err = run("docker ps -a --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'")
    print(out.strip())
    if err.strip(): print("ERR:", err.strip())

    section("compose ps (from infra dir)")
    out, err = run("cd /opt/agent-crosschain/infra && docker compose ps 2>&1")
    print(out.strip())
    if err.strip(): print("ERR:", err.strip())

    section("compose config services + profiles")
    out, err = run("cd /opt/agent-crosschain/infra && docker compose config --services 2>&1")
    print(out.strip())
    if err.strip(): print("ERR:", err.strip())

    section("service->profile mapping")
    out, err = run("cd /opt/agent-crosschain/infra && docker compose config 2>/dev/null | grep -E 'container_name|profiles:|^  [a-z]' | head -60")
    print(out.strip())
    if err.strip(): print("ERR:", err.strip())

if __name__ == "__main__":
    main()
