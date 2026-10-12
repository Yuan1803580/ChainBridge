from __future__ import annotations

import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Iterator

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .coordinator import coordinate
from .workflow_models import Workflow


_locks_guard = threading.Lock()
_workflow_locks: dict[str, threading.Lock] = {}


def _workflow_lock(workflow_id: str) -> threading.Lock:
    with _locks_guard:
        # FastAPI may enter and exit a synchronous generator dependency on
        # different worker threads. A plain Lock supports that handoff, while
        # RLock requires the releasing thread to be the acquiring thread.
        return _workflow_locks.setdefault(workflow_id, threading.Lock())


@contextmanager
def workflow_coordination_lock(
    workflow_id: str,
    *,
    blocking: bool = True,
) -> Iterator[bool]:
    lock = _workflow_lock(workflow_id)
    acquired = lock.acquire(blocking=blocking)
    try:
        yield acquired
    finally:
        if acquired:
            lock.release()


def workflow_coordination_guard(workflow_id: str) -> Iterator[None]:
    with workflow_coordination_lock(workflow_id, blocking=False) as acquired:
        if not acquired:
            raise HTTPException(
                status_code=409,
                detail="Workflow coordination is already in progress",
            )
        yield


class WorkflowRecoveryScheduler:
    """Continuously advances durable workflows without invoking Agents itself."""

    ACTIVE_STATUSES = ("RUNNING", "FAILED", "FAILED_REFUNDING")

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        enabled: bool,
        interval_seconds: float,
        max_workflows: int,
        max_actions: int,
    ) -> None:
        self._session_factory = session_factory
        self._enabled = enabled
        self._interval_seconds = interval_seconds
        self._max_workflows = max_workflows
        self._max_actions = max_actions
        self._wake_event = threading.Event()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._state_lock = threading.Lock()
        self._last_scan_at: datetime | None = None
        self._next_scan_at: datetime | None = None
        self._last_error: str | None = None
        self._scan_count = 0
        self._recovered_workflow_count = 0
        self._last_actions: dict[str, str] = {}

    def start(self) -> None:
        if not self._enabled or (self._thread is not None and self._thread.is_alive()):
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="workflow-recovery",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._wake_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=max(2.0, self._interval_seconds + 1.0))

    def wake(self, workflow_id: str | None = None) -> None:
        if workflow_id:
            with self._state_lock:
                self._last_actions.setdefault(workflow_id, "wake_requested")
        self._wake_event.set()

    def status(self) -> dict[str, object]:
        with self._state_lock:
            return {
                "enabled": self._enabled,
                "running": bool(self._thread and self._thread.is_alive()),
                "intervalSeconds": self._interval_seconds,
                "lastScanAt": self._serialize_time(self._last_scan_at),
                "nextScanAt": self._serialize_time(self._next_scan_at),
                "scanCount": self._scan_count,
                "recoveredWorkflowCount": self._recovered_workflow_count,
                "lastError": self._last_error,
                "lastActions": dict(self._last_actions),
            }

    @staticmethod
    def _serialize_time(value: datetime | None) -> str | None:
        return value.isoformat() if value is not None else None

    def run_once(self) -> list[dict[str, object]]:
        workflow_ids: list[str]
        with self._session_factory() as session:
            workflow_ids = list(
                session.scalars(
                    select(Workflow.id)
                    .where(Workflow.status.in_(self.ACTIVE_STATUSES))
                    .order_by(Workflow.updated_at.desc())
                    .limit(self._max_workflows)
                )
            )

        results: list[dict[str, object]] = []
        for workflow_id in workflow_ids:
            with workflow_coordination_lock(workflow_id, blocking=False) as acquired:
                if not acquired:
                    results.append({"workflowId": workflow_id, "action": "coordination_locked"})
                    continue
                try:
                    with self._session_factory() as session:
                        workflow = session.get(Workflow, workflow_id)
                        if workflow is None or workflow.status not in self.ACTIVE_STATUSES:
                            continue
                        actions = coordinate(session, workflow, self._max_actions)
                        session.commit()
                        action = str(actions[-1].get("action", "idle")) if actions else "idle"
                        results.append({"workflowId": workflow_id, "action": action, "actions": actions})
                        with self._state_lock:
                            self._last_actions[workflow_id] = action
                            if action not in {"idle", "waiting_for_agent"}:
                                self._recovered_workflow_count += 1
                except Exception as exc:
                    message = f"{type(exc).__name__}: {exc}"
                    results.append({"workflowId": workflow_id, "action": "recovery_error", "error": message})
                    with self._state_lock:
                        self._last_error = message
        with self._state_lock:
            self._last_scan_at = datetime.now(timezone.utc)
            self._next_scan_at = self._last_scan_at + timedelta(seconds=self._interval_seconds)
            self._scan_count += 1
            if len(self._last_actions) > 100:
                self._last_actions = dict(list(self._last_actions.items())[-100:])
        return results

    def _run(self) -> None:
        while not self._stop_event.is_set():
            self.run_once()
            self._wake_event.wait(self._interval_seconds)
            self._wake_event.clear()
