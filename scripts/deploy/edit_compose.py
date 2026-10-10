#!/usr/bin/env python3
"""Edit remote compose + .env: CORS from env, add admin + worker token vars."""
import paramiko
import os
import sys

HOST = os.environ.get("ECS_HOST", "<YOUR_SERVER_IP>")
PORT = int(os.environ.get("ECS_SSH_PORT", "22"))
USER = os.environ.get("ECS_USER", "root")
KEY = os.environ.get("ECS_SSH_KEY", os.path.expanduser("~/.ssh/ecs_key"))
TOKEN = os.environ.get("PROTOCOL_WORKER_API_TOKEN")
SHA = os.environ.get("PROTOCOL_WORKER_API_TOKEN_SHA256")
if not (TOKEN and SHA):
    sys.exit(
        "ERROR: PROTOCOL_WORKER_API_TOKEN / PROTOCOL_WORKER_API_TOKEN_SHA256 are not set. "
        "Export both from your secrets manager before running this script. "
        "The previously hardcoded values have been removed for security."
    )

def ssh():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, port=PORT, username=USER, key_filename=KEY, timeout=30)
    return c

def read(c, path):
    sftp = c.open_sftp(); f = sftp.open(path, "r"); data = f.read().decode(); f.close(); sftp.close()
    return data

def write(c, path, data):
    sftp = c.open_sftp(); f = sftp.open(path, "w"); f.write(data); f.close(); sftp.close()

c = ssh()

# --- edit compose ---
p = "/opt/agent-crosschain/infra/docker-compose.yml"
s = read(c, p)

# 1) CORS from env
s = s.replace(
    "CORS_ORIGINS: http://localhost:3000,http://127.0.0.1:3000",
    "CORS_ORIGINS: ${CORS_ORIGINS:-http://localhost:3000,http://127.0.0.1:3000}",
)

# 2) api: add admin + worker-sha after RELAYER_LEASE_SECONDS line
api_block = """      RELAYER_LEASE_SECONDS: ${RELAYER_LEASE_SECONDS:-300}
      PLATFORM_ADMIN_DID: ${PLATFORM_ADMIN_DID}
      PLATFORM_ADMIN_PUBLIC_KEY: ${PLATFORM_ADMIN_PUBLIC_KEY}
      PROTOCOL_WORKER_API_TOKEN_SHA256: ${PROTOCOL_WORKER_API_TOKEN_SHA256}"""
s = s.replace("      RELAYER_LEASE_SECONDS: ${RELAYER_LEASE_SECONDS:-300}\n", api_block + "\n", 1)

# 3) worker: add worker token + admin after RELAYER_SIGNER_MODE line
worker_block = """      RELAYER_SIGNER_MODE: ${RELAYER_SIGNER_MODE:-env}
      PROTOCOL_WORKER_API_TOKEN: ${PROTOCOL_WORKER_API_TOKEN}
      PLATFORM_ADMIN_DID: ${PLATFORM_ADMIN_DID}
      PLATFORM_ADMIN_PUBLIC_KEY: ${PLATFORM_ADMIN_PUBLIC_KEY}"""
s = s.replace("      RELAYER_SIGNER_MODE: ${RELAYER_SIGNER_MODE:-env}\n", worker_block + "\n", 1)

write(c, p, s)
print("compose updated")

# --- edit .env files ---
for envp in ["/opt/agent-crosschain/.env", "/opt/agent-crosschain/infra/.env"]:
    e = read(c, envp)
    for line in [
        f"PROTOCOL_WORKER_API_TOKEN={TOKEN}",
        f"PROTOCOL_WORKER_API_TOKEN_SHA256={SHA}",
    ]:
        key = line.split("=")[0]
        if key not in e:
            e = e.rstrip("\n") + "\n" + line + "\n"
    write(c, envp, e)
    print("updated", envp)

c.close()
