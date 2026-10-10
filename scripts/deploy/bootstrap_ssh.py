#!/usr/bin/env python3
"""Bootstrap SSH key auth to the Alibaba Cloud ECS, then run a command."""
import paramiko
import os
import sys

HOST = os.environ.get("ECS_HOST", "<YOUR_SERVER_IP>")
PORT = int(os.environ.get("ECS_SSH_PORT", "22"))
USER = os.environ.get("ECS_USER", "root")
PASSWORD = os.environ.get("ECS_ROOT_PASSWORD")
if not PASSWORD:
    sys.exit(
        "ERROR: ECS root password is not set. Export ECS_ROOT_PASSWORD "
        "(e.g. `export ECS_ROOT_PASSWORD='<secret>'`) before running this script. "
        "The previously hardcoded password has been removed for security."
    )
KEY_PATH = os.environ.get("ECS_SSH_KEY", os.path.expanduser("~/.ssh/ecs_key"))

def ensure_key():
    pub = KEY_PATH + ".pub"
    if not os.path.exists(KEY_PATH):
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.hazmat.primitives import serialization
        key = rsa.generate_private_key(public_exponent=65537, key_size=4096)
        priv_pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        pub_pem = key.public_key().public_bytes(
            serialization.Encoding.OpenSSH,
            serialization.PublicFormat.OpenSSH,
        )
        with open(KEY_PATH, "wb") as f:
            f.write(priv_pem)
        os.chmod(KEY_PATH, 0o600)
        with open(pub, "wb") as f:
            f.write(pub_pem)
        print("generated key")
    else:
        with open(KEY_PATH + ".pub") as f:
            pub_pem = f.read().encode()
        print("key exists")
    return pub_pem

def push_pubkey(pub_pem):
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, port=PORT, username=USER, password=PASSWORD, timeout=30)
    cmd = (
        "mkdir -p ~/.ssh && chmod 700 ~/.ssh && "
        "grep -qxF '%s' ~/.ssh/authorized_keys || echo '%s' >> ~/.ssh/authorized_keys; "
        "chmod 600 ~/.ssh/authorized_keys; echo PUBKEY_DONE"
    ) % (pub_pem.decode().strip(), pub_pem.decode().strip())
    stdin, stdout, stderr = c.exec_command(cmd)
    out = stdout.read().decode()
    print("push result:", out.strip())
    c.close()

def run(cmd, use_key=False):
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    if use_key:
        c.connect(HOST, port=PORT, username=USER, key_filename=KEY_PATH, timeout=30)
    else:
        c.connect(HOST, port=PORT, username=USER, password=PASSWORD, timeout=30)
    stdin, stdout, stderr = c.exec_command(cmd, get_pty=True)
    out = stdout.read().decode()
    err = stderr.read().decode()
    print("=== OUT ===\n" + out)
    if err.strip():
        print("=== ERR ===\n" + err)
    c.close()
    return out, err

if __name__ == "__main__":
    import sys
    pub = ensure_key()
    push_pubkey(pub)
    # fix /root perms to 700 (StrictModes friendly), reload sshd
    run("chmod 700 /root; chmod 700 /root/.ssh; "
        "systemctl restart ssh 2>/dev/null || service ssh restart 2>/dev/null; "
        "echo 'perms fixed & sshd reloaded'",
        use_key=False)
    # now test key auth from local
    try:
        run("echo KEY_AUTH_OK", use_key=True)
        print("KEY LOGIN SUCCESS")
    except Exception as e:
        print("KEY LOGIN FAILED:", e)
