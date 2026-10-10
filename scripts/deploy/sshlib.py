#!/usr/bin/env python3
"""Reusable SSH helper for deploying to the ECS via OpenSSH (Git Bash bundled ssh/scp).

Uses the system OpenSSH client instead of paramiko to avoid DSA/RSA key-handling
quirks in newer cryptography builds. The deploy key is an RSA 4096 OPENSSH key.
"""
import subprocess, os

HOST = os.environ.get("ECS_HOST", "<YOUR_SERVER_IP>")
PORT = int(os.environ.get("ECS_SSH_PORT", "22"))
USER = os.environ.get("ECS_USER", "root")
KEY = os.environ.get("ECS_SSH_KEY", os.path.expanduser("~/.ssh/ecs_key"))

_SSH_OPTS = [
    "-i", KEY,
    "-p", str(PORT),
    "-o", "StrictHostKeyChecking=no",
    "-o", "UserKnownHostsFile=/dev/null",
    "-o", "LogLevel=ERROR",
    "-o", "BatchMode=yes",
    "-o", "PubkeyAcceptedKeyTypes=+ssh-rsa",
    "-o", "HostKeyAlgorithms=+ssh-rsa",
]

def _ssh_target():
    return f"{USER}@{HOST}"

def run(cmd, get_pty=True, timeout=120):
    """Run a command on the remote host. Returns (stdout, stderr)."""
    ssh_cmd = ["ssh"] + _SSH_OPTS + [_ssh_target(), cmd]
    proc = subprocess.run(
        ssh_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        text=True,
    )
    return proc.stdout, proc.stderr

def push(local, remote):
    """Push a local file to remote via scp."""
    # scp uses -P (capital) for port; ssh uses -p (lowercase). Build scp-specific opts.
    scp_opts = [
        "-i", KEY,
        "-P", str(PORT),
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "LogLevel=ERROR",
        "-o", "PubkeyAcceptedKeyTypes=+ssh-rsa",
        "-o", "HostKeyAlgorithms=+ssh-rsa",
    ]
    scp_cmd = ["scp"] + scp_opts
    if os.path.isdir(local):
        scp_cmd.append("-r")
    scp_cmd += [local, f"{_ssh_target()}:{remote}"]
    proc = subprocess.run(scp_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"scp failed: {proc.stderr}")
    return True

def conn():
    # Kept for backwards compatibility; OpenSSH is connection-less per call.
    return None

if __name__ == "__main__":
    out, err = run("echo OK; whoami; hostname")
    print("OUT:", out)
    if err.strip():
        print("ERR:", err)
