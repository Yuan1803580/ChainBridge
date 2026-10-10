#!/usr/bin/env python3
"""Task 1: server init - swap, docker-compose check, firewall ports."""
from sshlib import run

INIT_SCRIPT = r"""
set -e
echo '=== 1. swap (2G) ==='
if [ ! -f /swapfile ]; then
  fallocate -l 2G /swapfile 2>/dev/null || dd if=/dev/zero of=/swapfile bs=1M count=2048
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
  echo 'swap created'
else
  echo 'swap already exists'
fi
swapon --show | head -2

echo '=== 2. docker & compose ==='
docker --version
if docker compose version >/dev/null 2>&1; then
  docker compose version
else
  echo 'docker compose v2 missing, installing plugin...'
  apt-get update -y && apt-get install -y docker-compose-plugin
  docker compose version
fi

echo '=== 3. ufw firewall ==='
ufw --version >/dev/null 2>&1 && {
  ufw allow 22/tcp
  ufw allow 3000/tcp
  ufw allow 8000/tcp
  ufw allow 80/tcp
  ufw allow 443/tcp
  ufw --force enable
  ufw status numbered | head -20
} || echo 'no ufw (likely Aliyun default image, rely on security group)'

echo '=== 4. python3 / node for deploy scripts ==='
python3 --version; node --version 2>/dev/null || echo 'no node (will use container)'

echo '=== DONE ==='
"""

if __name__ == "__main__":
    out, err = run(INIT_SCRIPT)
    print(out)
    if err.strip():
        print("ERR:\n", err)
