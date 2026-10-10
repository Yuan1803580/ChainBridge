#!/usr/bin/env python3
"""Diagnose key-auth failure on ECS via password login."""
import paramiko
import os
import sys

HOST = os.environ.get("ECS_HOST", "<YOUR_SERVER_IP>")
USER = os.environ.get("ECS_USER", "root")
PASSWORD = os.environ.get("ECS_ROOT_PASSWORD")
if not PASSWORD:
    sys.exit(
        "ERROR: ECS root password is not set. Export ECS_ROOT_PASSWORD "
        "(e.g. `export ECS_ROOT_PASSWORD='<secret>'`) before running this script. "
        "The previously hardcoded password has been removed for security."
    )

def run(cmd):
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, port=22, username=USER, password=PASSWORD, timeout=30)
    stdin, stdout, stderr = c.exec_command(cmd, get_pty=True)
    print(stdout.read().decode())
    e = stderr.read().decode()
    if e.strip():
        print("ERR:", e)
    c.close()

if __name__ == "__main__":
    run(r"""
echo '=== effective sshd config (key-related) ===';
sshd -T 2>/dev/null | grep -iE 'pubkey|permitroot|strickmodes|allowusers|denyusers|authorizedkeysfile' ;
echo '=== authorized_keys content (first 60 chars of each line) ===';
while read -r l; do echo "${l:0:60}..."; done < /root/.ssh/authorized_keys;
echo '=== owner of .ssh and authorized_keys ===';
ls -ld /root /root/.ssh /root/.ssh/authorized_keys;
echo '=== recent auth.log ===';
tail -15 /var/log/auth.log 2>/dev/null || tail -15 /var/log/secure 2>/dev/null || echo 'no auth log';
echo '=== does sshd actually listen & which port ===';
ss -tlnp | grep -E ':22 ' ;
""")