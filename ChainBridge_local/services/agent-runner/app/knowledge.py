from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class KnowledgeSection:
    source: str
    heading: str
    content: str


class ProjectKnowledgeIndex:
    """Read-only Markdown retrieval constrained to configured project paths."""

    def __init__(self, sections: list[KnowledgeSection]) -> None:
        self.sections = sections

    @classmethod
    def from_env(cls, project_root: Path) -> "ProjectKnowledgeIndex":
        configured = os.getenv("AGENT_KNOWLEDGE_PATHS", "").strip()
        roots = (
            [Path(item.strip()).expanduser().resolve() for item in configured.split(";") if item.strip()]
            if configured
            else [(project_root / "docs").resolve()]
        )
        sections: list[KnowledgeSection] = []
        for root in roots:
            files = sorted(root.rglob("*.md")) if root.is_dir() else [root]
            for path in files:
                if not path.is_file() or path.suffix.lower() != ".md" or path.stat().st_size > 250_000:
                    continue
                try:
                    content = path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                relative = str(path.relative_to(project_root)) if path.is_relative_to(project_root) else path.name
                chunks = re.split(r"(?=^#{1,4}\s+)", content, flags=re.MULTILINE)
                for chunk in chunks:
                    value = chunk.strip()
                    if not value:
                        continue
                    first_line = value.splitlines()[0]
                    heading = re.sub(r"^#{1,4}\s+", "", first_line).strip()
                    sections.append(KnowledgeSection(relative, heading, value))
        return cls(sections)

    @staticmethod
    def _terms(query: str) -> list[str]:
        stop_terms = {
            "agent",
            "agents",
            "current",
            "how",
            "project",
            "system",
            "the",
            "workflow",
        }
        terms = [
            item.lower()
            for item in re.findall(r"[A-Za-z][A-Za-z0-9_-]{1,}", query)
            if item.lower() not in stop_terms
        ]
        for chinese in re.findall(r"[\u4e00-\u9fff]+", query):
            if len(chinese) <= 3:
                terms.append(chinese)
            else:
                terms.extend(chinese[index : index + 2] for index in range(len(chinese) - 1))
        return list(dict.fromkeys(term for term in terms if term))

    def search(
        self, query: str, *, max_sections: int = 4, max_characters: int = 12_000
    ) -> dict[str, object]:
        terms = self._terms(query)
        normalized_query = query.lower()
        protocol_question = any(
            marker in normalized_query
            for marker in (
                "attempt",
                "ccip",
                "chain",
                "dex",
                "relay",
                "settlement",
                "vault",
                "失败",
                "结算",
                "跨链",
                "链",
                "超时",
            )
        )
        authority = {
            "EXPERIMENT_V2_RUNBOOK_ZH.md": 30,
            "IMPLEMENTATION_CONTRACT.md": 18,
            "LOCAL_EXPERIMENT.md": 12,
            "REVIEW_V2_AND_AGENT_SIGNING.md": 8,
        }
        ranked: list[tuple[int, int, KnowledgeSection]] = []
        for index, section in enumerate(self.sections):
            haystack = f"{section.heading}\n{section.content}".lower()
            heading = section.heading.lower()
            score = sum(min(haystack.count(term), 4) for term in terms)
            score += 3 * sum(min(heading.count(term), 2) for term in terms)
            if score:
                if protocol_question:
                    score += authority.get(Path(section.source).name, 0)
                ranked.append((score, index, section))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        selected = [item[2] for item in ranked[:max_sections]]
        remaining = max_characters
        excerpts: list[str] = []
        sources: list[dict[str, str]] = []
        for section in selected:
            prefix = f"[Source: {section.source} | {section.heading}]\n"
            excerpt = (prefix + section.content)[:remaining]
            if not excerpt:
                break
            excerpts.append(excerpt)
            sources.append({"path": section.source, "heading": section.heading})
            remaining -= len(excerpt)
            if remaining <= 0:
                break
        return {"content": "\n\n".join(excerpts), "sources": sources}
