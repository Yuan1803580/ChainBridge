from __future__ import annotations

import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class ChatSessionNotFound(LookupError):
    pass


class ChatSessionStore:
    """Small persistent store owned by one Agent Runner process."""

    def __init__(self, database_path: str) -> None:
        self.database_path = database_path
        if database_path != ":memory:":
            Path(database_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(database_path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock, self._connection:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS chat_sessions (
                    session_id TEXT PRIMARY KEY,
                    agent_slug TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    title TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS chat_messages (
                    message_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES chat_sessions(session_id)
                );
                CREATE INDEX IF NOT EXISTS ix_chat_messages_session
                    ON chat_messages(session_id, message_id);
                """
            )

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def create_session(
        self, *, agent_slug: str, owner_id: str, title: str | None
    ) -> dict[str, object]:
        session_id = str(uuid.uuid4())
        now = _utc_now()
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO chat_sessions (
                    session_id, agent_slug, owner_id, title, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (session_id, agent_slug, owner_id, title, now, now),
            )
        return {
            "sessionId": session_id,
            "agentId": agent_slug,
            "ownerId": owner_id,
            "title": title,
            "createdAt": now,
            "updatedAt": now,
            "messages": [],
        }

    def _session_row(self, session_id: str, agent_slug: str) -> sqlite3.Row:
        row = self._connection.execute(
            """
            SELECT session_id, agent_slug, owner_id, title, created_at, updated_at
            FROM chat_sessions WHERE session_id = ? AND agent_slug = ?
            """,
            (session_id, agent_slug),
        ).fetchone()
        if row is None:
            raise ChatSessionNotFound(session_id)
        return row

    def get_session(self, *, session_id: str, agent_slug: str) -> dict[str, object]:
        with self._lock:
            row = self._session_row(session_id, agent_slug)
            messages = self._connection.execute(
                """
                SELECT role, content, created_at
                FROM chat_messages WHERE session_id = ? ORDER BY message_id
                """,
                (session_id,),
            ).fetchall()
        return {
            "sessionId": row["session_id"],
            "agentId": row["agent_slug"],
            "ownerId": row["owner_id"],
            "title": row["title"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
            "messages": [
                {
                    "role": message["role"],
                    "content": message["content"],
                    "createdAt": message["created_at"],
                }
                for message in messages
            ],
        }

    def conversation_messages(
        self, *, session_id: str, agent_slug: str, max_turns: int
    ) -> list[dict[str, str]]:
        limit = max(1, max_turns) * 2
        with self._lock:
            self._session_row(session_id, agent_slug)
            rows = self._connection.execute(
                """
                SELECT role, content FROM (
                    SELECT message_id, role, content
                    FROM chat_messages
                    WHERE session_id = ?
                    ORDER BY message_id DESC
                    LIMIT ?
                ) ORDER BY message_id
                """,
                (session_id, limit),
            ).fetchall()
        return [
            {"role": str(row["role"]), "content": str(row["content"])}
            for row in rows
        ]

    def append_exchange(
        self,
        *,
        session_id: str,
        agent_slug: str,
        user_message: str,
        assistant_message: str,
    ) -> str:
        now = _utc_now()
        with self._lock, self._connection:
            self._session_row(session_id, agent_slug)
            self._connection.executemany(
                """
                INSERT INTO chat_messages (session_id, role, content, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    (session_id, "user", user_message, now),
                    (session_id, "assistant", assistant_message, now),
                ),
            )
            self._connection.execute(
                "UPDATE chat_sessions SET updated_at = ? WHERE session_id = ?",
                (now, session_id),
            )
        return now

    def delete_session(self, *, session_id: str, agent_slug: str) -> None:
        with self._lock, self._connection:
            self._session_row(session_id, agent_slug)
            self._connection.execute(
                "DELETE FROM chat_messages WHERE session_id = ?", (session_id,)
            )
            self._connection.execute(
                "DELETE FROM chat_sessions WHERE session_id = ?", (session_id,)
            )
