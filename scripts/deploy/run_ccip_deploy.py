#!/usr/bin/env python3
"""Orchestrate the real-CCIP contract deployment on the ECS testnets.

Order (with cross-dependencies):
  1. deploy-real-ccip-receiver.ts  (--network destinationTestnet) -> REAL_CCIP_RECEIVER_ADDRESS
  2. deploy-real-marketplace-escrow.ts (--network sourceTestnet)   -> REAL_MARKETPLACE_ESCROW_ADDRESS
  3. deploy-real-ccip-sender.ts    (--network sourceTestnet)       -> REAL_CCIP_SENDER_ADDRESS
  4. configure-real-ccip-receiver.ts (--network destinationTestnet) sets allowedRemoteSender=sender
  5. configure-real-marketplace-escrow.ts (--network sourceTestnet) sets realCcipConfig

Each produced address is written back into /opt/ccip-deploy/.env so later steps
see it, and at the end all four addresses (+AUTHORIZED_ESCROW) are written into
/opt/agent-crosschain/.env and /opt/agent-crosschain/infra/.env for the worker.
"""
import sys, re, time
sys.path.insert(0, ".")
from sshlib import run

DEPLOY_DIR = "/opt/ccip-deploy"

def sh(cmd, timeout=600):
    out, err = run(cmd, get_pty=True, timeout=timeout)
    return out, err

def run_hardhat(script, network, timeout=600):
    print(f"\n########## hardhat run {script} --network {network} ##########")
    out, err = sh(
        f"cd {DEPLOY_DIR} && npx hardhat run {script} --network {network} 2>&1",
        timeout=timeout,
    )
    if out.strip():
        print(out)
    if err.strip():
        print("[ERR]", err)
    return out + "\n" + err

def grab_addr(text, var):
    m = re.search(rf"{var}=0x[0-9a-fA-F]+", text)
    return m.group(0).split("=", 1)[1].strip() if m else None

def write_env(path, var, value):
    # create or replace the var line (handles empty placeholder lines)
    check, _ = sh(f"grep -q '^{var}=' {path}")
    if "0" in check:  # not present -> append
        sh(f"echo '{var}={value}' >> {path}")
    else:
        sh(f"sed -i 's|^{var}=.*|{var}={value}|' {path}")
    print(f"[env] {path}: {var}={value}")

def main():
    results = {}

    # Receiver already deployed on Arbitrum Sepolia (step 1, done separately to
    # conserve the small Arbitrum-Sepolia testnet balance). Reuse its address.
    raddr = "0x409c31b165aa2f12d47c5eb60fa59f73d5277778"
    results["REAL_CCIP_RECEIVER_ADDRESS"] = raddr
    write_env(f"{DEPLOY_DIR}/.env", "REAL_CCIP_RECEIVER_ADDRESS", raddr)
    print(f"[reuse] REAL_CCIP_RECEIVER_ADDRESS={raddr}")

    # 2) Escrow on source (Sepolia) - needs receiver address
    o2 = run_hardhat("deploy-real-marketplace-escrow.ts", "sourceTestnet")
    eaddr = grab_addr(o2, "REAL_MARKETPLACE_ESCROW_ADDRESS")
    if not eaddr:
        print("!! FAILED to capture REAL_MARKETPLACE_ESCROW_ADDRESS; aborting."); return
    results["REAL_MARKETPLACE_ESCROW_ADDRESS"] = eaddr
    results["AUTHORIZED_ESCROW"] = eaddr
    write_env(f"{DEPLOY_DIR}/.env", "REAL_MARKETPLACE_ESCROW_ADDRESS", eaddr)
    write_env(f"{DEPLOY_DIR}/.env", "AUTHORIZED_ESCROW", eaddr)

    # 3) Sender on source (Sepolia) - needs escrow + receiver
    o3 = run_hardhat("deploy-real-ccip-sender.ts", "sourceTestnet")
    saddr = grab_addr(o3, "REAL_CCIP_SENDER_ADDRESS")
    if not saddr:
        print("!! FAILED to capture REAL_CCIP_SENDER_ADDRESS; aborting."); return
    results["REAL_CCIP_SENDER_ADDRESS"] = saddr
    write_env(f"{DEPLOY_DIR}/.env", "REAL_CCIP_SENDER_ADDRESS", saddr)

    # 4) Configure receiver (destination) - allow sender
    o4 = run_hardhat("configure-real-ccip-receiver.ts", "destinationTestnet")

    # 5) Configure escrow (source) - wire sender + receiver
    o5 = run_hardhat("configure-real-marketplace-escrow.ts", "sourceTestnet")

    # Persist to the real project envs (worker reads these)
    for path in ["/opt/agent-crosschain/.env", "/opt/agent-crosschain/infra/.env"]:
        for k in ["REAL_CCIP_RECEIVER_ADDRESS", "REAL_MARKETPLACE_ESCROW_ADDRESS",
                  "REAL_CCIP_SENDER_ADDRESS", "AUTHORIZED_ESCROW"]:
            write_env(path, k, results[k])

    print("\n===== DEPLOYMENT COMPLETE =====")
    for k, v in results.items():
        print(f"{k}={v}")

if __name__ == "__main__":
    main()
