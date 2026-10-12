from __future__ import annotations

from pathlib import Path

from app.knowledge import ProjectKnowledgeIndex


def test_chain_c_failure_prioritizes_experiment_v2_runbook() -> None:
    project_root = Path(__file__).resolve().parents[3]
    index = ProjectKnowledgeIndex.from_env(project_root)

    result = index.search("链 C 上的 Agent 失败后系统如何处理？")

    assert result["sources"]
    assert result["sources"][0]["path"].endswith("EXPERIMENT_V2_RUNBOOK_ZH.md")
