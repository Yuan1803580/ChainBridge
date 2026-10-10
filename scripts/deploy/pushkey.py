import paramiko
import os
import sys
KEY_PUB_PATH = os.environ.get("ECS_SSH_KEY_PUB", os.path.expanduser("~/.ssh/ecs_key.pub"))
HOST = os.environ.get("ECS_HOST", "<YOUR_SERVER_IP>")
USER = os.environ.get("ECS_USER", "root")
PASS = os.environ.get("ECS_ROOT_PASSWORD")
if not PASS:
    sys.exit(
        "ERROR: ECS root password is not set. Export ECS_ROOT_PASSWORD "
        "(e.g. `export ECS_ROOT_PASSWORD='<secret>'`) before running this script. "
        "The previously hardcoded password has been removed for security."
    )
pub = open(KEY_PUB_PATH).read().strip()
c = paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, username=USER, password=PASS, timeout=30)
cmd = ("mkdir -p ~/.ssh && chmod 700 ~/.ssh && "
       "grep -qxF '%s' ~/.ssh/authorized_keys || echo '%s' >> ~/.ssh/authorized_keys; "
       "chmod 600 ~/.ssh/authorized_keys; echo DONE") % (pub, pub)
stdin, stdout, stderr = c.exec_command(cmd)
print("OUT:", stdout.read().decode().strip())
print("ERR:", stderr.read().decode().strip())
c.close()
