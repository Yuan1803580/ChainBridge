from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from app.result_quality import (
    QUALITY_POLICY_VERSION,
    acceptance_coverage,
    apply_attempt_quality,
    calculate_attempt_quality,
)
from app.coordinator import _review_result


def _calculate(*, result: dict, review_passed: bool = True, trust: str = "75") -> dict:
    return calculate_attempt_quality(
        acceptance_criteria={
            "requiredFields": ["summary", "artifacts.pdf"],
            "mustEqual": {"status": "ready"},
            "minScore": 80,
        },
        result_payload=result,
        result_hash="0x" + "1" * 64,
        review_passed=review_passed,
        matching_trust_score=trust,
        final=False,
    )


def test_quality_uses_core_evidence_and_ignores_agent_reported_score():
    low_reported = _calculate(result={
        "summary": "usable",
        "artifacts": {"pdf": "report.pdf"},
        "status": "ready",
        "score": 1,
    })
    high_reported = _calculate(result={
        "summary": "usable",
        "artifacts": {"pdf": "report.pdf"},
        "status": "ready",
        "score": 100,
    })

    assert low_reported["qualityScore"] == Decimal("95.00")
    assert high_reported["qualityScore"] == Decimal("95.00")
    assert low_reported["reportedScore"] == Decimal("1.00")
    assert high_reported["reportedScore"] == Decimal("100.00")
    assert low_reported["qualityBreakdown"]["reportedScore"]["includedInQuality"] is False


def test_completion_coverage_counts_nested_fields_and_must_equal_checks():
    coverage = acceptance_coverage(
        {"requiredFields": ["summary", "artifacts.pdf"], "mustEqual": {"status": "ready"}},
        {"summary": "usable", "artifacts": {}, "status": "draft"},
    )

    assert coverage["passedChecks"] == 1
    assert coverage["totalChecks"] == 3
    assert coverage["score"] == Decimal("33.33")


def test_legacy_agent_score_is_not_an_acceptance_check():
    coverage = acceptance_coverage(
        {"requiredFields": ["summary", "score"], "minScore": 99},
        {"summary": "usable", "score": 1},
    )

    assert coverage["score"] == Decimal("100.00")
    assert coverage["passedChecks"] == 1
    assert coverage["totalChecks"] == 1
    assert coverage["ignoredLegacyFields"] == ["score"]
    assert coverage["legacyMinScore"] == 99
    assert coverage["legacyMinScoreIncluded"] is False


def test_core_review_ignores_agent_score_and_supports_nested_fields():
    node = SimpleNamespace(
        acceptance_criteria={
            "requiredFields": ["summary", "artifacts.pdf", "score"],
            "mustEqual": {"status": "ready"},
            "minScore": 99,
        }
    )
    attempt = SimpleNamespace(
        result_payload={
            "summary": "usable",
            "artifacts": {"pdf": "report.pdf"},
            "status": "ready",
            "score": 1,
        }
    )

    passed, reason = _review_result(node, attempt)

    assert passed is True
    assert reason == "Immutable acceptance criteria passed"


def test_agent_score_alone_is_not_a_business_result():
    coverage = acceptance_coverage(
        {"requiredFields": ["score"], "mustEqual": {"score": 100}, "minScore": 100},
        {"score": 100},
    )

    assert coverage["score"] == Decimal("0.00")
    assert coverage["ignoredLegacyFields"] == ["score"]


def test_failed_review_has_final_auditable_score():
    calculated = calculate_attempt_quality(
        acceptance_criteria={"requiredFields": ["summary"]},
        result_payload={"summary": "submitted", "score": 100},
        result_hash="0x" + "2" * 64,
        review_passed=False,
        matching_trust_score="80",
        final=True,
    )

    assert calculated["qualityScore"] == Decimal("66.00")
    assert calculated["qualityStatus"] == "FINAL"
    assert calculated["qualityPolicyVersion"] == QUALITY_POLICY_VERSION
    assert calculated["qualityEvidenceHash"].startswith("0x")
    assert len(calculated["qualityEvidenceHash"]) == 66


def test_same_inputs_produce_same_score_and_evidence_hash():
    first = _calculate(result={"summary": "same", "artifacts": {"pdf": "a.pdf"}, "status": "ready"})
    second = _calculate(result={"status": "ready", "artifacts": {"pdf": "a.pdf"}, "summary": "same"})

    assert first["qualityScore"] == second["qualityScore"]
    assert first["qualityEvidenceHash"] == second["qualityEvidenceHash"]


def test_attempts_keep_independent_quality_records():
    node = SimpleNamespace(acceptance_criteria={"requiredFields": ["summary"]})
    failed = SimpleNamespace(
        result_payload={},
        result_hash="0x" + "3" * 64,
        matching_trust_score=Decimal("70"),
    )
    replacement = SimpleNamespace(
        result_payload={"summary": "complete"},
        result_hash="0x" + "4" * 64,
        matching_trust_score=Decimal("90"),
    )

    apply_attempt_quality(failed, node, review_passed=False, final=True)
    apply_attempt_quality(replacement, node, review_passed=True, final=True)

    assert failed.quality_score == Decimal("14.00")
    assert replacement.quality_score == Decimal("98.00")
    assert failed.quality_evidence_hash != replacement.quality_evidence_hash
    assert failed.quality_status == replacement.quality_status == "FINAL"
