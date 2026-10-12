from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from .services import canonical_hash


QUALITY_POLICY_VERSION = "NODE_RESULT_QUALITY_V1"
LEGACY_REPORTED_SCORE_FIELD = "score"
QUALITY_WEIGHTS = {
    "completion": Decimal("0.50"),
    "review": Decimal("0.30"),
    "trust": Decimal("0.20"),
}
SCORE_QUANTUM = Decimal("0.01")


def _bounded_score(value: Any) -> Decimal:
    try:
        score = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0")
    return min(max(score, Decimal("0")), Decimal("100"))


def extract_reported_score(result_payload: dict[str, Any] | None) -> Decimal | None:
    if not isinstance(result_payload, dict):
        return None
    value = result_payload.get("score")
    if value is None or isinstance(value, bool):
        return None
    try:
        return _bounded_score(value).quantize(SCORE_QUANTUM, rounding=ROUND_HALF_UP)
    except InvalidOperation:
        return None


def _path_exists(payload: dict[str, Any], path: str) -> bool:
    current: Any = payload
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return False
        current = current[part]
    return True


def _path_value(payload: dict[str, Any], path: str) -> Any:
    current: Any = payload
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def acceptance_coverage(
    acceptance_criteria: dict[str, Any] | None,
    result_payload: dict[str, Any] | None,
) -> dict[str, Any]:
    criteria = acceptance_criteria if isinstance(acceptance_criteria, dict) else {}
    result = result_payload if isinstance(result_payload, dict) else {}
    checks: list[dict[str, Any]] = []

    configured_required_fields = sorted({str(field) for field in criteria.get("requiredFields", [])})
    required_fields = [field for field in configured_required_fields if field != LEGACY_REPORTED_SCORE_FIELD]
    ignored_legacy_fields = [
        field for field in configured_required_fields if field == LEGACY_REPORTED_SCORE_FIELD
    ]
    for field in required_fields:
        checks.append({
            "kind": "REQUIRED_FIELD",
            "field": field,
            "passed": _path_exists(result, field),
        })

    must_equal = criteria.get("mustEqual", {})
    if isinstance(must_equal, dict):
        for field in sorted(must_equal):
            if field == LEGACY_REPORTED_SCORE_FIELD:
                if field not in ignored_legacy_fields:
                    ignored_legacy_fields.append(field)
                continue
            expected = must_equal[field]
            actual = _path_value(result, field)
            checks.append({
                "kind": "MUST_EQUAL",
                "field": field,
                "passed": _path_exists(result, field) and actual == expected,
                "expectedHash": canonical_hash(expected),
                "actualHash": canonical_hash(actual),
            })

    if not checks:
        trusted_result = {
            key: value for key, value in result.items() if key != LEGACY_REPORTED_SCORE_FIELD
        }
        checks.append({
            "kind": "RESULT_SUBMITTED",
            "field": "output",
            "passed": bool(trusted_result),
        })

    passed_checks = sum(1 for check in checks if check["passed"])
    total_checks = len(checks)
    score = (Decimal(passed_checks) * Decimal("100") / Decimal(total_checks)).quantize(
        SCORE_QUANTUM,
        rounding=ROUND_HALF_UP,
    )
    return {
        "score": score,
        "passedChecks": passed_checks,
        "totalChecks": total_checks,
        "checks": checks,
        "ignoredLegacyFields": ignored_legacy_fields,
        "legacyMinScore": criteria.get("minScore"),
        "legacyMinScoreIncluded": False,
    }


def calculate_attempt_quality(
    *,
    acceptance_criteria: dict[str, Any] | None,
    result_payload: dict[str, Any] | None,
    result_hash: str | None,
    review_passed: bool,
    matching_trust_score: Any,
    final: bool,
    matching_trust_source: str = "MATCH_TIME_PROFILE_SNAPSHOT",
) -> dict[str, Any]:
    completion = acceptance_coverage(acceptance_criteria, result_payload)
    completion_score = completion["score"]
    review_score = Decimal("100") if review_passed else Decimal("0")
    trust_score = _bounded_score(matching_trust_score).quantize(SCORE_QUANTUM, rounding=ROUND_HALF_UP)
    contributions = {
        "completion": (completion_score * QUALITY_WEIGHTS["completion"]).quantize(SCORE_QUANTUM),
        "review": (review_score * QUALITY_WEIGHTS["review"]).quantize(SCORE_QUANTUM),
        "trust": (trust_score * QUALITY_WEIGHTS["trust"]).quantize(SCORE_QUANTUM),
    }
    quality_score = sum(contributions.values(), Decimal("0")).quantize(
        SCORE_QUANTUM,
        rounding=ROUND_HALF_UP,
    )
    reported_score = extract_reported_score(result_payload)
    quality_status = "FINAL" if final else "PROVISIONAL"
    breakdown = {
        "formula": "0.50 * completion + 0.30 * review + 0.20 * matchingTrust",
        "weights": {name: str(weight) for name, weight in QUALITY_WEIGHTS.items()},
        "components": {
            "completion": {
                "score": str(completion_score),
                "contribution": str(contributions["completion"]),
                "passedChecks": completion["passedChecks"],
                "totalChecks": completion["totalChecks"],
                "checks": completion["checks"],
                "ignoredLegacyFields": completion["ignoredLegacyFields"],
                "legacyMinScore": completion["legacyMinScore"],
                "legacyMinScoreIncluded": False,
            },
            "review": {
                "score": str(review_score),
                "contribution": str(contributions["review"]),
                "decision": "PASS" if review_passed else "FAIL",
            },
            "matchingTrust": {
                "score": str(trust_score),
                "contribution": str(contributions["trust"]),
                "source": matching_trust_source,
            },
        },
        "reportedScore": {
            "value": str(reported_score) if reported_score is not None else None,
            "trusted": False,
            "includedInQuality": False,
        },
    }
    evidence = {
        "policyVersion": QUALITY_POLICY_VERSION,
        "qualityStatus": quality_status,
        "qualityScore": str(quality_score),
        "resultHash": result_hash or canonical_hash(result_payload or {}),
        "criteriaHash": canonical_hash(acceptance_criteria or {}),
        "breakdown": breakdown,
    }
    return {
        "reportedScore": reported_score,
        "qualityScore": quality_score,
        "qualityStatus": quality_status,
        "qualityBreakdown": breakdown,
        "qualityPolicyVersion": QUALITY_POLICY_VERSION,
        "qualityEvidenceHash": canonical_hash(evidence),
        "qualityCalculatedAt": datetime.now(timezone.utc),
    }


def apply_attempt_quality(attempt: Any, node: Any, *, review_passed: bool, final: bool) -> dict[str, Any]:
    calculated = calculate_attempt_quality(
        acceptance_criteria=node.acceptance_criteria,
        result_payload=attempt.result_payload,
        result_hash=attempt.result_hash,
        review_passed=review_passed,
        matching_trust_score=attempt.matching_trust_score,
        matching_trust_source=getattr(attempt, "matching_trust_source", None) or "MATCH_TIME_PROFILE_SNAPSHOT",
        final=final,
    )
    attempt.reported_score = calculated["reportedScore"]
    attempt.quality_score = calculated["qualityScore"]
    attempt.quality_status = calculated["qualityStatus"]
    attempt.quality_breakdown = calculated["qualityBreakdown"]
    attempt.quality_policy_version = calculated["qualityPolicyVersion"]
    attempt.quality_evidence_hash = calculated["qualityEvidenceHash"]
    attempt.quality_calculated_at = calculated["qualityCalculatedAt"]
    return calculated
