from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation

from sqlalchemy import select

from app.config import Settings
from app.database import build_session_factory
from app.result_quality import apply_attempt_quality
from app.workflow_models import AgentMatchCandidate, NodeAttempt, WorkflowAgentProfile, WorkflowNode


def _historical_trust(session, attempt: NodeAttempt) -> tuple[Decimal, str]:
    candidate = session.get(AgentMatchCandidate, attempt.match_candidate_id) if attempt.match_candidate_id else None
    if candidate is not None:
        weighted_trust = (candidate.score_breakdown or {}).get("trust")
        try:
            return Decimal(str(weighted_trust)) / Decimal("0.20"), "MATCH_CANDIDATE_BREAKDOWN_RECONSTRUCTED"
        except (InvalidOperation, TypeError, ValueError):
            pass
    profile = session.get(WorkflowAgentProfile, attempt.agent_profile_id)
    if profile is not None:
        return Decimal(str(profile.trust_score)), "CURRENT_PROFILE_FALLBACK"
    return Decimal("0"), "UNAVAILABLE_FALLBACK_ZERO"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill auditable node-result quality scores.")
    parser.add_argument(
        "--recalculate-existing",
        action="store_true",
        help="Recalculate existing quality records using the current immutable policy.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = Settings.from_env()
    factory = build_session_factory(settings.database_url)
    updated = 0
    skipped = 0
    with factory() as session:
        attempts = list(session.scalars(select(NodeAttempt).order_by(NodeAttempt.created_at, NodeAttempt.id)))
        for attempt in attempts:
            if (not args.recalculate_existing and attempt.quality_status != "NOT_CALCULATED") or attempt.result_payload is None:
                skipped += 1
                continue
            if attempt.review_status not in {"PASSED", "FAILED"}:
                skipped += 1
                continue
            node = session.get(WorkflowNode, attempt.node_id)
            if node is None:
                skipped += 1
                continue
            trust_score, trust_source = _historical_trust(session, attempt)
            attempt.matching_trust_score = trust_score
            attempt.matching_trust_source = trust_source
            final = attempt.review_status == "FAILED" or attempt.status in {"SETTLED", "FAILED", "REVIEW_FAILED"}
            apply_attempt_quality(
                attempt,
                node,
                review_passed=attempt.review_status == "PASSED",
                final=final,
            )
            updated += 1
        session.commit()
    print(f"Node result quality backfill complete: updated={updated}, skipped={skipped}")


if __name__ == "__main__":
    main()
