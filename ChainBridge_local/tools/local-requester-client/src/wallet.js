import { existsSync } from "node:fs";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { stdin, stderr } from "node:process";
import { Wallet, getAddress } from "ethers";

async function hiddenPrompt(label) {
  if (!stdin.isTTY || !stdin.setRawMode) {
    throw new Error("Set REQUESTER_WALLET_PASSWORD when running without an interactive terminal");
  }
  stderr.write(label);
  stdin.setRawMode(true);
  stdin.resume();
  stdin.setEncoding("utf8");
  return new Promise((resolve, reject) => {
    let value = "";
    const cleanup = () => {
      stdin.setRawMode(false);
      stdin.pause();
      stdin.removeListener("data", onData);
      stderr.write("\n");
    };
    const onData = (input) => {
      for (const character of input) {
        if (character === "\r" || character === "\n") {
          cleanup();
          resolve(value);
          return;
        }
        if (character === "\u0003") {
          cleanup();
          reject(new Error("Cancelled"));
          return;
        }
        if (character === "\u007f" || character === "\b") {
          value = value.slice(0, -1);
        } else {
          value += character;
        }
      }
    };
    stdin.on("data", onData);
  });
}

export async function walletPassword({ confirm = false } = {}) {
  const configured = process.env.REQUESTER_WALLET_PASSWORD;
  if (configured) {
    if (configured.length < 10) throw new Error("REQUESTER_WALLET_PASSWORD must contain at least 10 characters");
    return configured;
  }
  const first = await hiddenPrompt("Wallet password: ");
  if (first.length < 10) throw new Error("Wallet password must contain at least 10 characters");
  if (confirm) {
    const second = await hiddenPrompt("Confirm password: ");
    if (first !== second) throw new Error("Wallet passwords do not match");
  }
  return first;
}

export async function createWallet(file, { overwrite = false } = {}) {
  if (existsSync(file) && !overwrite) throw new Error(`Encrypted wallet already exists: ${file}`);
  const password = await walletPassword({ confirm: true });
  const wallet = Wallet.createRandom();
  const encrypted = await wallet.encrypt(password);
  await mkdir(path.dirname(file), { recursive: true });
  await writeFile(file, encrypted, { encoding: "utf8", mode: 0o600, flag: overwrite ? "w" : "wx" });
  return { address: wallet.address, keystore: file };
}

export async function walletAddress(file) {
  const encrypted = JSON.parse(await readFile(file, "utf8"));
  if (!encrypted.address) throw new Error("Keystore has no address");
  return getAddress(`0x${encrypted.address.replace(/^0x/, "")}`);
}

export async function unlockWallet(file, provider) {
  const password = await walletPassword();
  return unlockWalletWithPassword(file, password, provider);
}

export async function unlockWalletWithPassword(file, password, provider) {
  const wallet = await Wallet.fromEncryptedJson(await readFile(file, "utf8"), password);
  return provider ? wallet.connect(provider) : wallet;
}
