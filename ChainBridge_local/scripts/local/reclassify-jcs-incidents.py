from __future__ import annotations

import json
import os
from pathlib import Path

from sqlalchemy import select

from app.coordinator import _event
from app.database import build_session_factory
from app.models import AuditLog, utcnow
from app.reputation import recompute_agent_reputation
from app.workflow_models import (
    FailureIncident,
    NodeAttempt,
    Workflow,
    WorkflowAgentProfile,
    WorkflowNode,
)


WORKFLOW_ID = "031f38c6-5219-4d13-9612-225281389198"
NODE_ID = "4c190887-4bd9-4656-8d35-4083ee58daad"
RECLASSIFICATIONS = {
    "a282374e-adc3-46cc-a1ce-64668ef0fe09": "5f5c2e27-1ec8-4fb2-82cc-18d2a6beb9cf",
    "2a099aca-efcb-4adc-b922-fe820595717a": "88f230df-7b0d-4e3b-bde7-38ae1916bdde",
}
NEW_REASON = "RESULT_CANONICALIZATION_MISMATCH"


def _database_url() -> str:
    configured = os.getenv("DATABASE_URL")
    if configured:
        return configured
    project_root = Path(__file__).resolve().parents[2]
    database_path = project_root / ".runtime" / "local-experiment" / "agent-market.db"
    return "sqlite:///" + database_path.as_posix()


def main() -> None:
    session_factory = build_session_factory(_database_url())
    now = utcnow()
    summaries: dict[str, dict[str, object]] = {}

    with session_factory() as session:
        workflow = session.get(Workflow, WORKFLOW_ID)
        node = session.get(WorkflowNode, NODE_ID)
        if workflow is None or node is None or node.workflow_id != WORKFLOW_ID:
            raise RuntimeError("WF-1021 N2 was not found in the active database")

        for attempt_id, incident_id in RECLASSIFICATIONS.items():
            attempt = session.get(NodeAttempt, attempt_id)
            incident = session.get(FailureIncident, incident_id)
            if (
                attempt is None
                or incident is None
                or attempt.node_id != NODE_ID
                or incident.attempt_id != attempt_id
            ):
                raise RuntimeError(f"Attempt/incident binding is invalid for {attempt_id}")
            if incident.reason_code not in {"AGENT_INVALID_SIGNATURE", NEW_REASON}:
                raise RuntimeError(
                    f"Refusing to reclassify unexpected reason {incident.reason_code}"
                )

            original_reason = incident.reason_code
            if original_reason == NEW_REASON:
                profile = session.get(WorkflowAgentProfile, attempt.agent_profile_id)
                if profile is None:
                    raise RuntimeError(f"Agent profile is missing for {attempt_id}")
                summary, _ = recompute_agent_reputation(
                    session,
                    profile,
                    trigger_attempt_id=attempt.id,
                )
                summaries[profile.agent_key] = summary
                continue

            evidence = dict(incident.evidence or {})
            evidence.update(
                {
                    "originalReasonCode": "AGENT_INVALID_SIGNATURE",
                    "reclassificationReason": (
                        "Python and Worker used different JSON key ordering while "
                        "verifying an otherwise valid Agent result"
                    ),
                    "canonicalizationStandard": "RFC8785",
                    "reclassifiedAt": now.isoformat().replace("+00:00", "Z"),
                }
            )
            incident.reason_code = NEW_REASON
            incident.responsible_party = "PLATFORM"
            incident.retryable = True
            incident.agent_fault = False
            incident.gas_treatment = "PLATFORM_GAS_TREASURY"
            incident.route_paused = False
            incident.evidence = evidence
            incident.resolved_at = now

            attempt.failure_reason_code = NEW_REASON
            attempt.failure_responsible_party = "PLATFORM"
            session.add(
                AuditLog(
                    actor_id=None,
                    action="workflow.failure.reclassified",
                    resource_type="node_attempt",
                    resource_id=attempt.id,
                    details={
                        "workflowId": WORKFLOW_ID,
                        "nodeId": NODE_ID,
                        "incidentId": incident.id,
                        "fromReasonCode": original_reason,
                        "toReasonCode": NEW_REASON,
                        "responsibleParty": "PLATFORM",
                        "agentFault": False,
                        "gasTreatment": "PLATFORM_GAS_TREASURY",
                    },
                )
            )
            _event(
                session,
                workflow,
                "FAILURE_RECLASSIFIED",
                actor="ProtocolMaintainer",
                node=node,
                attempt=attempt,
                payload={
                    "incidentId": incident.id,
                    "fromReasonCode": original_reason,
                    "toReasonCode": NEW_REASON,
                    "responsibleParty": "PLATFORM",
                    "agentFault": False,
                },
            )

            profile = session.get(WorkflowAgentProfile, attempt.agent_profile_id)
            if profile is None:
                raise RuntimeError(f"Agent profile is missing for {attempt_id}")
            summary, _ = recompute_agent_reputation(
                session,
                profile,
                trigger_attempt_id=attempt.id,
            )
            summaries[profile.agent_key] = summary

        session.commit()

    print(
        json.dumps(
            {
                "workflowId": WORKFLOW_ID,
                "nodeId": NODE_ID,
                "reasonCode": NEW_REASON,
                "attempts": list(RECLASSIFICATIONS),
                "reputation": summaries,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
