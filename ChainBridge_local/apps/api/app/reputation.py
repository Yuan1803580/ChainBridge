from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import AuditLog, utcnow
from .workflow_models import NodeAttempt, WorkflowAgentProfile


REPUTATION_POLICY_VERSION = "AGENT_REPUTATION_V1"
COLD_START_TRUST_SCORE = 75
CONFIDENCE_SAMPLE_TARGET = 10
SUCCESS_PRIOR_SUCCESSES = 3
SUCCESS_PRIOR_FAILURES = 1

AGENT_FAILURE_QUALITY = {
    "AGENT_INVALID_SIGNATURE": 0,
    "AGENT_RESULT_REJECTED": 20,
    "AGENT_EXECUTION_ERROR": 30,
    "AGENT_TIMEOUT": 40,
}


def _clamp(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(value, maximum))


def _round_ratio(numerator: int, denominator: int) -> int:
    return (numerator + denominator // 2) // denominator


def _parse_utc_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def calculate_agent_reputation(
    attempts: Iterable[NodeAttempt],
    *,
    baseline_trust_score: int = COLD_START_TRUST_SCORE,
    epoch_started_at: datetime | None = None,
) -> dict[str, Any]:
    baseline = _clamp(int(baseline_trust_score), 0, 100)
    successes = 0
    failures = 0
    excluded = 0
    historical_excluded = 0
    quality_total = 0
    failure_reasons: dict[str, int] = {}

    for attempt in attempts:
        if epoch_started_at is not None:
            attempt_created_at = attempt.created_at
            if attempt_created_at.tzinfo is None:
                attempt_created_at = attempt_created_at.replace(tzinfo=timezone.utc)
            else:
                attempt_created_at = attempt_created_at.astimezone(timezone.utc)
            if attempt_created_at < epoch_started_at:
                historical_excluded += 1
                continue
        if attempt.status == "SETTLED":
            successes += 1
            quality_total += 100
            continue
        if (
            attempt.status in {"FAILED", "REVIEW_FAILED"}
            and attempt.failure_responsible_party == "AGENT"
        ):
            failures += 1
            reason = attempt.failure_reason_code or "AGENT_FAILURE"
            failure_reasons[reason] = failure_reasons.get(reason, 0) + 1
            quality_total += AGENT_FAILURE_QUALITY.get(reason, 25)
            continue
        excluded += 1

    sample_count = successes + failures
    if sample_count == 0:
        trust_score = baseline
        success_rate_bps = 0
    else:
        confidence_units = min(sample_count, CONFIDENCE_SAMPLE_TARGET)
        trust_numerator = (
            baseline * (CONFIDENCE_SAMPLE_TARGET - confidence_units) * sample_count
            + quality_total * confidence_units
        )
        trust_score = _round_ratio(
            trust_numerator,
            CONFIDENCE_SAMPLE_TARGET * sample_count,
        )
        success_rate_bps = _round_ratio(
            (successes + SUCCESS_PRIOR_SUCCESSES) * 10_000,
            sample_count + SUCCESS_PRIOR_SUCCESSES + SUCCESS_PRIOR_FAILURES,
        )

    return {
        "policyVersion": REPUTATION_POLICY_VERSION,
        "baselineTrustScore": baseline,
        "sampleCount": sample_count,
        "successfulAttempts": successes,
        "failedAttempts": failures,
        "excludedAttempts": excluded,
        "historicalAttemptsExcluded": historical_excluded,
        "failureReasonCounts": [
            {"reasonCode": reason_code, "count": count}
            for reason_code, count in sorted(failure_reasons.items())
        ],
        "successRateBps": _clamp(success_rate_bps, 0, 10_000),
        "trustScore": _clamp(trust_score, 0, 100),
    }


def initialize_agent_reputation(
    profile: WorkflowAgentProfile,
    *,
    baseline_trust_score: int = COLD_START_TRUST_SCORE,
) -> dict[str, Any]:
    now = utcnow().isoformat().replace("+00:00", "Z")
    summary = calculate_agent_reputation(
        [],
        baseline_trust_score=baseline_trust_score,
    )
    summary["epochStartedAt"] = now
    summary["updatedAt"] = now
    metadata = dict(profile.metadata_json or {})
    metadata["reputation"] = summary
    profile.metadata_json = metadata
    profile.trust_score = summary["trustScore"]
    profile.success_rate_bps = summary["successRateBps"]
    return summary


def recompute_agent_reputation(
    session: Session,
    profile: WorkflowAgentProfile,
    *,
    trigger_attempt_id: str | None = None,
) -> tuple[dict[str, Any], bool]:
    session.flush()
    metadata = dict(profile.metadata_json or {})
    previous = dict(metadata.get("reputation") or {})
    epoch_started_at_raw = previous.get("epochStartedAt")
    epoch_started_at = _parse_utc_timestamp(epoch_started_at_raw)
    baseline = _clamp(
        int(previous.get("baselineTrustScore", profile.trust_score or COLD_START_TRUST_SCORE)),
        0,
        100,
    )
    attempts = list(
        session.scalars(
            select(NodeAttempt)
            .where(NodeAttempt.agent_profile_id == profile.id)
            .order_by(NodeAttempt.created_at, NodeAttempt.id)
        )
    )
    calculated = calculate_agent_reputation(
        attempts,
        baseline_trust_score=baseline,
        epoch_started_at=epoch_started_at,
    )
    if epoch_started_at_raw:
        calculated["epochStartedAt"] = epoch_started_at_raw
    previous_core = {key: previous.get(key) for key in calculated}
    changed = (
        previous_core != calculated
        or profile.trust_score != calculated["trustScore"]
        or profile.success_rate_bps != calculated["successRateBps"]
    )
    if not changed:
        return previous, False

    summary = {
        **calculated,
        "updatedAt": utcnow().isoformat().replace("+00:00", "Z"),
    }
    metadata["reputation"] = summary
    profile.metadata_json = metadata
    old_score = profile.trust_score
    old_success_rate = profile.success_rate_bps
    profile.trust_score = calculated["trustScore"]
    profile.success_rate_bps = calculated["successRateBps"]
    session.add(
        AuditLog(
            actor_id=None,
            action="agent.reputation.recalculated",
            resource_type="workflow_agent_profile",
            resource_id=profile.id,
            details={
                "policyVersion": REPUTATION_POLICY_VERSION,
                "triggerAttemptId": trigger_attempt_id,
                "previousTrustScore": old_score,
                "previousSuccessRateBps": old_success_rate,
                "current": summary,
            },
        )
    )
    return summary, True


def refresh_all_agent_reputations(session: Session) -> int:
    changed = 0
    for profile in session.scalars(
        select(WorkflowAgentProfile).order_by(WorkflowAgentProfile.id)
    ):
        _, was_changed = recompute_agent_reputation(session, profile)
        changed += int(was_changed)
    return changed
