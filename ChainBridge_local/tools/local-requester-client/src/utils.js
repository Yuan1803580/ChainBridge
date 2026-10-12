import { createHash, randomUUID } from "node:crypto";
import { mkdir, readFile, rename, writeFile } from "node:fs/promises";
import path from "node:path";

export function canonicalJson(value) {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value !== null && typeof value === "object") {
    const entries = Object.entries(value).sort(([left], [right]) => (left < right ? -1 : left > right ? 1 : 0));
    return `{${entries.map(([key, child]) => `${JSON.stringify(key)}:${canonicalJson(child)}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

export function canonicalHash(value) {
  return `0x${createHash("sha256").update(canonicalJson(value), "utf8").digest("hex")}`;
}

export async function readJson(file) {
  return JSON.parse(await readFile(file, "utf8"));
}

export async function writeJsonAtomic(file, value) {
  await mkdir(path.dirname(file), { recursive: true });
  const temporary = `${file}.${process.pid}.tmp`;
  await writeFile(temporary, `${JSON.stringify(value, null, 2)}\n`, { encoding: "utf8", mode: 0o600 });
  await rename(temporary, file);
}

export function parseArgs(argv) {
  const result = { _: [] };
  for (let index = 0; index < argv.length; index += 1) {
    const value = argv[index];
    if (!value.startsWith("--")) {
      result._.push(value);
      continue;
    }
    const [rawKey, inline] = value.slice(2).split("=", 2);
    if (inline !== undefined) {
      result[rawKey] = inline;
    } else if (argv[index + 1] && !argv[index + 1].startsWith("--")) {
      result[rawKey] = argv[index + 1];
      index += 1;
    } else {
      result[rawKey] = true;
    }
  }
  return result;
}

export function newRequestId(prefix) {
  return `${prefix}:${randomUUID()}`;
}

export function redact(value) {
  if (Array.isArray(value)) return value.map(redact);
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).map(([key, child]) => {
      const sensitive = /(private|mnemonic|password|api.?key|access.?token|runtimeEnvelope|signature)/i.test(key);
      return [key, sensitive && typeof child !== "boolean" ? "[REDACTED]" : redact(child)];
    }));
  }
  return value;
}

export function fail(message) {
  throw new Error(message);
}

export function sleep(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}
