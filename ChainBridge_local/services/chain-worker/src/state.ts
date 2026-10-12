import { mkdirSync } from "node:fs";
import { dirname } from "node:path";
import { DatabaseSync } from "node:sqlite";

export type PendingEvent = {
  eventId: string;
  chainKey: string;
  payload: Record<string, unknown>;
  attempts: number;
};

export type Submission = {
  taskId: string;
  transactionHash: string;
  nonce?: number;
  rawTransaction?: `0x${string}`;
  status: "prepared" | "broadcast";
  workerId?: string;
  firstBroadcastAt: string;
  lastCheckedAt: string;
};

export class StateStore {
  private db: DatabaseSync;

  constructor(path: string) {
    if (path !== ":memory:") mkdirSync(dirname(path), { recursive: true });
    this.db = new DatabaseSync(path);
    this.db.exec(`
      PRAGMA journal_mode = WAL;
      PRAGMA busy_timeout = 5000;
      CREATE TABLE IF NOT EXISTS cursors (
        chain_key TEXT PRIMARY KEY,
        next_block TEXT NOT NULL
      );
      CREATE TABLE IF NOT EXISTS pending_events (
        event_id TEXT PRIMARY KEY,
        chain_key TEXT NOT NULL DEFAULT 'unknown',
        payload TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        attempts INTEGER NOT NULL DEFAULT 0,
        next_retry_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        last_error TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
      );
      CREATE TABLE IF NOT EXISTS processed_events (
        event_id TEXT PRIMARY KEY,
        chain_key TEXT NOT NULL,
        payload TEXT NOT NULL,
        block_number TEXT,
        block_hash TEXT,
        status TEXT NOT NULL DEFAULT 'confirmed',
        processed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
      );
      CREATE TABLE IF NOT EXISTS relayer_submissions (
        task_id TEXT PRIMARY KEY,
        transaction_hash TEXT NOT NULL,
        nonce INTEGER,
        raw_transaction TEXT,
        status TEXT NOT NULL DEFAULT 'broadcast',
        worker_id TEXT,
        first_broadcast_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        last_checked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
      );
    `);
    this.ensureColumn("pending_events", "chain_key", "TEXT NOT NULL DEFAULT 'unknown'");
    this.ensureColumn("pending_events", "status", "TEXT NOT NULL DEFAULT 'pending'");
    this.ensureColumn("pending_events", "next_retry_at", "TEXT");
    this.ensureColumn("relayer_submissions", "nonce", "INTEGER");
    this.ensureColumn("relayer_submissions", "raw_transaction", "TEXT");
    this.ensureColumn("relayer_submissions", "status", "TEXT NOT NULL DEFAULT 'broadcast'");
    this.ensureColumn("relayer_submissions", "worker_id", "TEXT");
    this.ensureColumn("relayer_submissions", "first_broadcast_at", "TEXT");
    this.ensureColumn("relayer_submissions", "last_checked_at", "TEXT");
    this.db.exec(`
      UPDATE pending_events SET next_retry_at = CURRENT_TIMESTAMP WHERE next_retry_at IS NULL;
      UPDATE relayer_submissions SET first_broadcast_at = CURRENT_TIMESTAMP WHERE first_broadcast_at IS NULL;
      UPDATE relayer_submissions SET last_checked_at = CURRENT_TIMESTAMP WHERE last_checked_at IS NULL;
    `);
  }

  private ensureColumn(table: string, column: string, definition: string): void {
    const columns = this.db.prepare(`PRAGMA table_info(${table})`).all() as Array<{ name: string }>;
    if (!columns.some((item) => item.name === column)) {
      this.db.exec(`ALTER TABLE ${table} ADD COLUMN ${column} ${definition}`);
    }
  }

  getCursor(chainKey: string, fallback: bigint): bigint {
    const row = this.db.prepare("SELECT next_block FROM cursors WHERE chain_key = ?").get(chainKey) as
      | { next_block: string }
      | undefined;
    return row ? BigInt(row.next_block) : fallback;
  }

  getStoredCursor(chainKey: string): bigint | undefined {
    const row = this.db.prepare("SELECT next_block FROM cursors WHERE chain_key = ?").get(chainKey) as
      | { next_block: string }
      | undefined;
    return row ? BigInt(row.next_block) : undefined;
  }

  setCursor(chainKey: string, nextBlock: bigint): void {
    this.db.prepare(`
      INSERT INTO cursors(chain_key, next_block) VALUES(?, ?)
      ON CONFLICT(chain_key) DO UPDATE SET next_block = excluded.next_block
    `).run(chainKey, nextBlock.toString());
  }

  enqueueEvent(payload: Record<string, unknown>, chainKey = "unknown"): void {
    const eventId = String(payload.eventId);
    const processed = this.db.prepare("SELECT 1 FROM processed_events WHERE event_id = ?").get(eventId);
    if (processed) return;
    this.db.prepare(
      "INSERT OR IGNORE INTO pending_events(event_id, chain_key, payload) VALUES(?, ?, ?)",
    ).run(eventId, chainKey, JSON.stringify(payload));
  }

  pendingEvents(now = new Date()): PendingEvent[] {
    const rows = this.db.prepare(`
      SELECT event_id, chain_key, payload, attempts
      FROM pending_events
      WHERE status = 'pending' AND next_retry_at <= ?
      ORDER BY created_at, event_id
    `).all(now.toISOString()) as Array<{
      event_id: string;
      chain_key: string;
      payload: string;
      attempts: number;
    }>;
    return rows.map((row) => ({
      eventId: row.event_id,
      chainKey: row.chain_key,
      payload: JSON.parse(row.payload),
      attempts: row.attempts,
    }));
  }

  hasPendingEvents(chainKey: string, fromBlock: bigint, toBlock: bigint): boolean {
    const rows = this.db.prepare(
      "SELECT payload FROM pending_events WHERE chain_key = ? AND status = 'pending'",
    ).all(chainKey) as Array<{ payload: string }>;
    return rows.some((row) => {
      const block = BigInt(String(JSON.parse(row.payload).blockNumber ?? "0"));
      return block >= fromBlock && block <= toBlock;
    });
  }

  acknowledgeEvent(eventId: string): void {
    const row = this.db.prepare(
      "SELECT chain_key, payload FROM pending_events WHERE event_id = ?",
    ).get(eventId) as { chain_key: string; payload: string } | undefined;
    if (!row) return;
    const payload = JSON.parse(row.payload) as Record<string, unknown>;
    this.db.exec("BEGIN IMMEDIATE");
    try {
      this.db.prepare(`
        INSERT OR REPLACE INTO processed_events(
          event_id, chain_key, payload, block_number, block_hash, status, processed_at
        ) VALUES(?, ?, ?, ?, ?, 'confirmed', CURRENT_TIMESTAMP)
      `).run(
        eventId,
        row.chain_key,
        row.payload,
        payload.blockNumber == null ? null : String(payload.blockNumber),
        payload.blockHash == null ? null : String(payload.blockHash),
      );
      this.db.prepare("DELETE FROM pending_events WHERE event_id = ?").run(eventId);
      this.db.exec("COMMIT");
    } catch (error) {
      this.db.exec("ROLLBACK");
      throw error;
    }
  }

  failEvent(
    eventId: string,
    error: string,
    maxAttempts = 8,
    baseDelayMs = 1_000,
  ): "pending" | "dead" {
    const row = this.db.prepare("SELECT attempts FROM pending_events WHERE event_id = ?").get(eventId) as
      | { attempts: number }
      | undefined;
    if (!row) return "dead";
    const attempts = row.attempts + 1;
    const status = attempts >= maxAttempts ? "dead" : "pending";
    const delay = Math.min(baseDelayMs * (2 ** Math.max(0, attempts - 1)), 300_000);
    const jitter = Math.floor(delay * Math.random() * 0.2);
    const nextRetryAt = new Date(Date.now() + delay + jitter).toISOString();
    this.db.prepare(`
      UPDATE pending_events
      SET attempts = ?, status = ?, next_retry_at = ?, last_error = ?
      WHERE event_id = ?
    `).run(attempts, status, nextRetryAt, error.slice(0, 5000), eventId);
    return status as "pending" | "dead";
  }

  deadLetters(): PendingEvent[] {
    const rows = this.db.prepare(`
      SELECT event_id, chain_key, payload, attempts
      FROM pending_events WHERE status = 'dead' ORDER BY created_at, event_id
    `).all() as Array<{ event_id: string; chain_key: string; payload: string; attempts: number }>;
    return rows.map((row) => ({
      eventId: row.event_id,
      chainKey: row.chain_key,
      payload: JSON.parse(row.payload),
      attempts: row.attempts,
    }));
  }

  replayDeadLetter(eventId: string): boolean {
    const result = this.db.prepare(`
      UPDATE pending_events
      SET status = 'pending', attempts = 0, next_retry_at = CURRENT_TIMESTAMP, last_error = NULL
      WHERE event_id = ? AND status = 'dead'
    `).run(eventId);
    return Number(result.changes) === 1;
  }

  recentProcessedEvents(chainKey: string, minimumBlock: bigint): Array<{
    eventId: string;
    payload: Record<string, unknown>;
    blockNumber: bigint;
    blockHash: string;
  }> {
    const rows = this.db.prepare(`
      SELECT event_id, payload, block_number, block_hash
      FROM processed_events
      WHERE chain_key = ? AND status = 'confirmed' AND block_number IS NOT NULL AND block_hash IS NOT NULL
    `).all(chainKey) as Array<{
      event_id: string;
      payload: string;
      block_number: string;
      block_hash: string;
    }>;
    return rows
      .filter((row) => BigInt(row.block_number) >= minimumBlock)
      .map((row) => ({
        eventId: row.event_id,
        payload: JSON.parse(row.payload),
        blockNumber: BigInt(row.block_number),
        blockHash: row.block_hash,
      }));
  }

  markProcessedOrphaned(eventId: string): void {
    this.db.prepare("UPDATE processed_events SET status = 'orphaned' WHERE event_id = ?").run(eventId);
  }

  savePreparedSubmission(
    taskId: string,
    transactionHash: string,
    nonce: number,
    rawTransaction: `0x${string}`,
    workerId: string,
  ): void {
    const result = this.db.prepare(`
      INSERT INTO relayer_submissions(
        task_id, transaction_hash, nonce, raw_transaction, status, worker_id,
        first_broadcast_at, last_checked_at
      ) VALUES(?, ?, ?, ?, 'prepared', ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
      ON CONFLICT(task_id) DO UPDATE SET
        last_checked_at = CURRENT_TIMESTAMP
      WHERE relayer_submissions.transaction_hash = excluded.transaction_hash
    `).run(taskId, transactionHash, nonce, rawTransaction, workerId);
    if (Number(result.changes) !== 1) {
      throw new Error(`submission_conflict: task ${taskId} is already bound to another transaction`);
    }
  }

  markSubmissionBroadcast(taskId: string, transactionHash: string): void {
    const result = this.db.prepare(`
      UPDATE relayer_submissions
      SET status = 'broadcast', last_checked_at = CURRENT_TIMESTAMP
      WHERE task_id = ? AND transaction_hash = ?
    `).run(taskId, transactionHash);
    if (Number(result.changes) !== 1) throw new Error(`submission ${taskId} was not prepared`);
  }

  saveSubmission(taskId: string, transactionHash: string, nonce?: number): void {
    const result = this.db.prepare(`
      INSERT INTO relayer_submissions(
        task_id, transaction_hash, nonce, status, first_broadcast_at, last_checked_at
      ) VALUES(?, ?, ?, 'broadcast', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
      ON CONFLICT(task_id) DO UPDATE SET
        last_checked_at = CURRENT_TIMESTAMP
      WHERE relayer_submissions.transaction_hash = excluded.transaction_hash
    `).run(taskId, transactionHash, nonce ?? null);
    if (Number(result.changes) !== 1) {
      throw new Error(`submission_conflict: task ${taskId} is already bound to another transaction`);
    }
  }

  getSubmission(taskId: string): string | undefined {
    return this.getSubmissionRecord(taskId)?.transactionHash;
  }

  getSubmissionRecord(taskId: string): Submission | undefined {
    const row = this.db.prepare(`
      SELECT task_id, transaction_hash, nonce, raw_transaction, status, worker_id,
             first_broadcast_at, last_checked_at
      FROM relayer_submissions WHERE task_id = ?
    `).get(taskId) as {
      task_id: string;
      transaction_hash: string;
      nonce: number | null;
      raw_transaction: `0x${string}` | null;
      status: "prepared" | "broadcast";
      worker_id: string | null;
      first_broadcast_at: string;
      last_checked_at: string;
    } | undefined;
    if (!row) return undefined;
    return {
      taskId: row.task_id,
      transactionHash: row.transaction_hash,
      nonce: row.nonce ?? undefined,
      rawTransaction: row.raw_transaction ?? undefined,
      status: row.status,
      workerId: row.worker_id ?? undefined,
      firstBroadcastAt: row.first_broadcast_at,
      lastCheckedAt: row.last_checked_at,
    };
  }

  touchSubmission(taskId: string): void {
    this.db.prepare(
      "UPDATE relayer_submissions SET last_checked_at = CURRENT_TIMESTAMP WHERE task_id = ?",
    ).run(taskId);
  }

  clearSubmission(taskId: string): void {
    this.db.prepare("DELETE FROM relayer_submissions WHERE task_id = ?").run(taskId);
  }

  metrics(): { pendingEvents: number; deadLetters: number; submissions: number; preparedSubmissions: number } {
    const count = (table: string, where = "") => Number(
      (this.db.prepare(`SELECT COUNT(*) AS count FROM ${table} ${where}`).get() as { count: number }).count,
    );
    return {
      pendingEvents: count("pending_events", "WHERE status = 'pending'"),
      deadLetters: count("pending_events", "WHERE status = 'dead'"),
      submissions: count("relayer_submissions"),
      preparedSubmissions: count("relayer_submissions", "WHERE status = 'prepared'"),
    };
  }

  close(): void {
    this.db.close();
  }
}
