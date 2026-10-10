#!/usr/bin/env python3
"""Task 2: upload project tarball to server and extract to /opt/agent-crosschain."""
from sshlib import run, push
import os

LOCAL_TAR = "D:/agent/agent-crosschain-platform-review-v2-agent-signing-complete-20260804(1)/agent-crosschain-platform/scripts/deploy/project_upload.tar.gz"
REMOTE_TAR = "/tmp/project_upload.tar.gz"
REMOTE_DIR = "/opt/agent-crosschain"

def main():
    print("uploading tarball (38M) ...")
    push(LOCAL_TAR, REMOTE_TAR)
    print("uploaded.")
    out, err = run(f"""
set -e
mkdir -p {REMOTE_DIR}
tar -xzf {REMOTE_TAR} -C {REMOTE_DIR}
echo 'extracted to {REMOTE_DIR}'
ls {REMOTE_DIR} | head
echo '=== sizes ==='
du -sh {REMOTE_DIR}
""")
    print(out)
    if err.strip():
        print("ERR:\n", err)

if __name__ == "__main__":
    main()
