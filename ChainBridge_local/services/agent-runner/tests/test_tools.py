from __future__ import annotations

import asyncio

from app.knowledge import ProjectKnowledgeIndex
from app.tools import ControlledToolRegistry


def test_controlled_tools_read_only_allowlist_and_audit(monkeypatch, tmp_path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "runbook.md").write_text("# Settlement\n\nUse attempt-level retry.", encoding="utf-8")
    outside = tmp_path / "secret.txt"
    outside.write_text("must not be exposed", encoding="utf-8")
    monkeypatch.setenv("AGENT_KNOWLEDGE_PATHS", str(docs))
    registry = ControlledToolRegistry(tmp_path, ProjectKnowledgeIndex.from_env(tmp_path))

    result, audit = asyncio.run(
        registry.execute("read_project_file", {"path": "docs/runbook.md"})
    )
    assert "attempt-level retry" in result["content"]
    assert audit["ok"] is True
    assert audit["argumentsHash"].startswith("sha256:")
    assert audit["durationMs"] >= 0

    rejected, rejected_audit = asyncio.run(
        registry.execute("read_project_file", {"path": "secret.txt"})
    )
    assert "outside" in rejected["error"]
    assert rejected_audit["ok"] is False


def test_controlled_tools_analyze_numbers(monkeypatch, tmp_path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    monkeypatch.setenv("AGENT_KNOWLEDGE_PATHS", str(docs))
    registry = ControlledToolRegistry(tmp_path, ProjectKnowledgeIndex.from_env(tmp_path))

    result, audit = asyncio.run(
        registry.execute("analyze_numbers", {"numbers": [1, 2, 3, 4]})
    )

    assert result["mean"] == 2.5
    assert result["median"] == 2.5
    assert audit["ok"] is True


def test_browser_tool_rejects_loopback_before_request(monkeypatch, tmp_path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    monkeypatch.setenv("AGENT_KNOWLEDGE_PATHS", str(docs))
    registry = ControlledToolRegistry(tmp_path, ProjectKnowledgeIndex.from_env(tmp_path))

    result, audit = asyncio.run(
        registry.execute("browse_url", {"url": "https://127.0.0.1/private"})
    )

    assert "non-public" in result["error"]
    assert audit["ok"] is False
