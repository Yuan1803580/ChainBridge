from __future__ import annotations

import pytest

from app.workflow_api import (
    _agent_protocol_headers,
    _agent_protocol_version,
    _manifest_snapshot,
)


def v2_manifest() -> dict[str, object]:
    return {
        "protocolVersion": "2.0",
        "protocol": {
            "canonicalization": "RFC8785-JCS",
            "resultSignature": "ES256K",
        },
        "agentId": "agent-c",
        "did": "did:local:agent-c",
        "capabilities": [],
    }


def test_v2_protocol_is_negotiated_and_preserved_in_snapshot():
    manifest = v2_manifest()

    assert _agent_protocol_version(manifest) == "2.0"
    assert _agent_protocol_headers(manifest) == {
        "X-Agent-Protocol-Version": "2.0",
        "Accept-Agent-Protocol": "2.0",
    }
    snapshot = _manifest_snapshot(manifest)
    assert snapshot["protocolVersion"] == "2.0"
    assert snapshot["protocol"]["canonicalization"] == "RFC8785-JCS"


def test_v1_manifest_remains_supported_during_migration():
    assert _agent_protocol_version({"agentId": "legacy"}) == "1.0"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("canonicalization", "locale-json"),
        ("resultSignature", "none"),
    ],
)
def test_v2_manifest_rejects_incompatible_security_contract(field: str, value: str):
    manifest = v2_manifest()
    manifest["protocol"][field] = value

    with pytest.raises(ValueError):
        _agent_protocol_version(manifest)
