from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session


PROJECT_ROOT = Path(__file__).resolve().parents[2]
API_ROOT = PROJECT_ROOT / "apps" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.reputation import recompute_agent_reputation  # noqa: E402
from app.workflow_models import FailureIncident, NodeAttempt, WorkflowAgentProfile  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True)
    parser.add_argument("--attempt-id", required=True)
    args = parser.parse_args()

    database = Path(args.database).resolve()
    engine = create_engine(f"sqlite:///{database.as_posix()}")
    now = datetime.now(timezone.utc)
    with Session(engine) as session:
        attempt = session.get(NodeAttempt, args.attempt_id)
        if attempt is None:
            raise SystemExit("Attempt not found")
        incident = session.scalar(
            select(FailureIncident).where(FailureIncident.attempt_id == attempt.id)
        )
        if incident is None:
            raise SystemExit("Failure incident not found")
        evidence = dict(incident.evidence or {})
        detail = str(evidence.get("detail", "")).lower()
        policy_limit = "tool" in detail and "round" in detail and "exceed" in detail
        already_reclassified = (
            attempt.failure_reason_code == "AGENT_EXECUTION_POLICY_LIMIT"
            and attempt.failure_responsible_party == "PLATFORM"
        )
        if not policy_limit and not already_reclassified:
            raise SystemExit("Attempt evidence is not a tool-round policy-limit failure")

        original_reason = attempt.failure_reason_code
        attempt.failure_reason_code = "AGENT_EXECUTION_POLICY_LIMIT"
        attempt.failure_responsible_party = "PLATFORM"
        incident.reason_code = "AGENT_EXECUTION_POLICY_LIMIT"
        incident.responsible_party = "PLATFORM"
        incident.retryable = True
        incident.agent_fault = False
        incident.gas_treatment = "PLATFORM_GAS_TREASURY"
        incident.route_paused = False
        incident.resolved_at = now
        incident.evidence = {
            **evidence,
            "originalReasonCode": original_reason,
            "reclassificationReason": (
                "The platform Runner imposed a fixed tool-round limit before the task deadline"
            ),
            "reclassifiedAt": now.isoformat().replace("+00:00", "Z"),
        }

        profile = session.get(WorkflowAgentProfile, attempt.agent_profile_id)
        if profile is None:
            raise SystemExit("Agent profile not found")
        reputation, _ = recompute_agent_reputation(
            session,
            profile,
            trigger_attempt_id=attempt.id,
        )
        session.commit()
        print(
            json.dumps(
                {
                    "attemptId": attempt.id,
                    "reasonCode": attempt.failure_reason_code,
                    "responsibleParty": attempt.failure_responsible_party,
                    "agentFault": incident.agent_fault,
                    "trustScore": reputation["trustScore"],
                    "sampleCount": reputation["sampleCount"],
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
