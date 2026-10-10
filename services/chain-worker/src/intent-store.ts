import { createHash, randomUUID } from "node:crypto";
import { existsSync, linkSync, mkdirSync, readFileSync, unlinkSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

export type UnsignedTransactionIntent = Record<string, unknown> & {
  requestId: string;
};

type StoredIntent = {
  schemaVersion: 1;
  requestId: string;
  intentHash: string;
  intent: UnsignedTransactionIntent;
};

function stable(value: unknown): string {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(stable).join(",")}]`;
  const object = value as Record<string, unknown>;
  return `{${Object.keys(object).sort().map((key) => `${JSON.stringify(key)}:${stable(object[key])}`).join(",")}}`;
}

function intentHash(intent: UnsignedTransactionIntent): string {
  return createHash("sha256").update(stable(intent)).digest("hex");
}

export class AgentIntentStore {
  private readonly directory: string;

  constructor(runtimeDirectory: string) {
    this.directory = resolve(runtimeDirectory, "agent-transaction-intents");
    mkdirSync(this.directory, { recursive: true });
  }

  private path(requestId: string): string {
    const name = createHash("sha256").update(requestId).digest("hex");
    return resolve(this.directory, `${name}.json`);
  }

  load(requestId: string): UnsignedTransactionIntent | undefined {
    const path = this.path(requestId);
    if (!existsSync(path)) return undefined;
    const stored = JSON.parse(readFileSync(path, "utf8")) as StoredIntent;
    if (
      stored.schemaVersion !== 1
      || stored.requestId !== requestId
      || stored.intent?.requestId !== requestId
      || stored.intentHash !== intentHash(stored.intent)
    ) {
      throw new Error(`Persisted Agent transaction intent ${requestId} failed its integrity check`);
    }
    return stored.intent;
  }

  saveIfAbsent(requestId: string, intent: UnsignedTransactionIntent): UnsignedTransactionIntent {
    if (intent.requestId !== requestId) throw new Error("Agent transaction intent requestId is inconsistent");
    const existing = this.load(requestId);
    if (existing) return existing;

    const path = this.path(requestId);
    const temporary = `${path}.${randomUUID()}.tmp`;
    const stored: StoredIntent = {
      schemaVersion: 1,
      requestId,
      intentHash: intentHash(intent),
      intent,
    };
    writeFileSync(temporary, `${JSON.stringify(stored, null, 2)}\n`, { encoding: "utf8", flag: "wx" });
    try {
      // A hard link publishes the complete temporary file without replacing an
      // intent another Worker process may have persisted for the same request.
      linkSync(temporary, path);
      return intent;
    } catch (error) {
      const concurrent = this.load(requestId);
      if (concurrent) return concurrent;
      throw error;
    } finally {
      try { unlinkSync(temporary); } catch { /* best-effort temporary cleanup */ }
    }
  }

  async getOrCreate(
    requestId: string,
    create: () => Promise<UnsignedTransactionIntent>,
  ): Promise<UnsignedTransactionIntent> {
    const existing = this.load(requestId);
    if (existing) return existing;
    return this.saveIfAbsent(requestId, await create());
  }
}

export function canonicalUtcTimestamp(value: Date): string {
  const iso = value.toISOString();
  if (iso.endsWith(".000Z")) return iso.slice(0, -5) + "Z";
  return iso.replace(/\.(\d{3})Z$/, ".$1000Z");
}
