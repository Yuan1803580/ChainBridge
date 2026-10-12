from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.coordinator import (
    _output_schema_supports_acceptance,
    _profile_capability_output_schema,
    _validate_attempt_output_contract,
)


RESEARCH_SCHEMA = {
    "type": "object",
    "required": ["summary", "score", "sources"],
    "properties": {
        "summary": {"type": "string"},
        "score": {"type": "integer", "minimum": 0, "maximum": 100},
        "sources": {"type": "array", "items": {"type": "string"}},
    },
    "additionalProperties": False,
}


def external_profile():
    return SimpleNamespace(
        metadata_json={
            "verification": {
                "manifest": {
                    "capabilities": [{
                        "capabilityId": "research",
                        "outputSchema": RESEARCH_SCHEMA,
                    }]
                }
            }
        }
    )


def test_manifest_schema_rejects_undeclared_acceptance_fields():
    assert not _output_schema_supports_acceptance(
        RESEARCH_SCHEMA,
        {
            "requiredFields": [
                "comprehensiveSummary",
                "keyRules",
                "documentSources",
            ]
        },
    )


def test_manifest_schema_accepts_codex_compatible_research_fields():
    assert _output_schema_supports_acceptance(
        RESEARCH_SCHEMA,
        {"requiredFields": ["summary", "score", "sources"]},
    )
    assert _profile_capability_output_schema(external_profile(), "research") == RESEARCH_SCHEMA


def test_manifest_schema_does_not_treat_optional_fields_as_guaranteed_output():
    optional_sources = {
        **RESEARCH_SCHEMA,
        "required": ["summary", "score"],
    }

    assert not _output_schema_supports_acceptance(
        optional_sources,
        {"requiredFields": ["summary", "score", "sources"]},
    )


def test_submitted_output_must_match_verified_manifest_schema():
    node = SimpleNamespace(capability="research")
    with pytest.raises(HTTPException) as error:
        _validate_attempt_output_contract(
            external_profile(),
            node,
            {"summary": "Evidence found.", "score": 90},
        )

    assert error.value.status_code == 422
    assert error.value.detail["code"] == "agent_output_schema_violation"


def test_valid_codex_compatible_output_passes_runtime_validation():
    node = SimpleNamespace(capability="research")
    _validate_attempt_output_contract(
        external_profile(),
        node,
        {
            "summary": "Evidence found.",
            "score": 90,
            "sources": ["IMPLEMENTATION_CONTRACT.md#Result contract"],
        },
    )
