#!/usr/bin/env python3
"""Check worker API_URL and whether it can reach the API; check API settlement mode + addresses."""
from sshlib import run

def section(t):
    print("\n" + "=" * 70); print(t); print("=" * 70)

def main():
    section("A. worker API_URL + reachability")
    out, _ = run("docker inspect agent-crosschain-shop-chain-worker-1 "
                 "--format '{{range .Config.Env}}{{println .}}{{end}}' | grep -i API_URL")
    print("worker API_URL =", out.strip() or "(default http://127.0.0.1:8000)")
    # try reaching api from inside the worker container
    for url in ("http://api:8000/health", "http://127.0.0.1:8000/health", "http://agent-crosschain-shop-api-1:8000/health"):
        out, err = run(f"docker exec agent-crosschain-shop-chain-worker-1 sh -c \"curl -s -m 8 -o /dev/null -w '{url} => HTTP %{{http_code}}\\\\n' {url}\" 2>&1")
        print(out.strip() or f"{url} => (no resp) {err.strip()[:80]}")

    section("B. api container API_URL / listening port")
    out, _ = run("docker ps --filter name=api --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'")
    print(out.strip() or "(api not running)")
    out, _ = run("docker inspect agent-crosschain-shop-api-1 --format '{{range .Config.Env}}{{println .}}{{end}}' | grep -iE 'API_URL|PORT|SETTLEMENT|REAL_|HOST'")
    print(out.strip())

    section("C. api reachable from host (nginx :80 /api)")
    out, _ = run("curl -s -m 10 -o /dev/null -w 'HTTP %{http_code}\n' http://127.0.0.1:8000/health; curl -s -m 10 http://127.0.0.1:8000/health; echo")
    print(out.strip())

if __name__ == "__main__":
    main()
