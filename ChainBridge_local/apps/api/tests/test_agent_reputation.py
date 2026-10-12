from datetime import datetime, timezone
from types import SimpleNamespace

from app.reputation import calculate_agent_reputation


def _attempt(
    status: str,
    *,
    reason: str | None = None,
    party: str | None = None,
    created_at: datetime | None = None,
):
    return SimpleNamespace(
        status=status,
        failure_reason_code=reason,
        failure_responsible_party=party,
        created_at=created_at or datetime(2026, 8, 10, tzinfo=timezone.utc),
    )


def test_reputation_starts_at_cold_start_without_claiming_a_success_rate():
    result = calculate_agent_reputation([], baseline_trust_score=75)

    assert result["trustScore"] == 75
    assert result["successRateBps"] == 0
    assert result["sampleCount"] == 0


def test_reputation_uses_terminal_agent_outcomes_and_excludes_platform_faults():
    result = calculate_agent_reputation(
        [
            _attempt("SETTLED"),
            _attempt("FAILED", reason="AGENT_TIMEOUT", party="AGENT"),
            _attempt("SYSTEM_RETRY", reason="RPC_FAILURE", party="PLATFORM"),
            _attempt("RESULT_SUBMITTED"),
        ],
        baseline_trust_score=75,
    )

    assert result["successfulAttempts"] == 1
    assert result["failedAttempts"] == 1
    assert result["excludedAttempts"] == 2
    assert result["sampleCount"] == 2
    assert result["successRateBps"] == 6667
    assert result["trustScore"] == 74
    assert result["failureReasonCounts"] == [
        {"reasonCode": "AGENT_TIMEOUT", "count": 1},
    ]


def test_invalid_signature_is_penalized_more_than_a_timeout():
    timeout = calculate_agent_reputation(
        [_attempt("FAILED", reason="AGENT_TIMEOUT", party="AGENT")]
    )
    invalid_signature = calculate_agent_reputation(
        [_attempt("FAILED", reason="AGENT_INVALID_SIGNATURE", party="AGENT")]
    )

    assert invalid_signature["trustScore"] < timeout["trustScore"]


def test_platform_canonicalization_failures_do_not_affect_agent_reputation():
    result = calculate_agent_reputation(
        [
            _attempt(
                "FAILED",
                reason="RESULT_CANONICALIZATION_MISMATCH",
                party="PLATFORM",
            ),
        ]
    )

    assert result["sampleCount"] == 0
    assert result["failedAttempts"] == 0
    assert result["excludedAttempts"] == 1
    assert result["trustScore"] == 75


def test_reputation_epoch_excludes_restored_historical_attempts():
    result = calculate_agent_reputation(
        [
            _attempt("SETTLED", created_at=datetime(2026, 8, 9, tzinfo=timezone.utc)),
            _attempt("SETTLED", created_at=datetime(2026, 8, 11, tzinfo=timezone.utc)),
        ],
        epoch_started_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
    )

    assert result["sampleCount"] == 1
    assert result["successfulAttempts"] == 1
    assert result["historicalAttemptsExcluded"] == 1
