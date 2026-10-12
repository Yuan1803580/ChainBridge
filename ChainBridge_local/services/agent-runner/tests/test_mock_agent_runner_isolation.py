from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("AGENT_RUNNER_ALLOW_INSECURE_LOCAL", "true")

from app.main import build_app


@pytest.mark.parametrize(
    ("runner_id", "slug", "other_slugs"),
    [
        (
            "runner-b",
            "workflow-research-b",
            ("workflow-verification-c", "workflow-verification-d"),
        ),
        (
            "runner-c",
            "workflow-verification-c",
            ("workflow-research-b", "workflow-verification-d"),
        ),
        (
            "runner-d",
            "workflow-verification-d",
            ("workflow-research-b", "workflow-verification-c"),
        ),
    ],
)
def test_runner_hosts_only_selected_mock_agent(
    monkeypatch: pytest.MonkeyPatch,
    runner_id: str,
    slug: str,
    other_slugs: tuple[str, str],
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("AGENT_RUNNER_SLUGS", slug)
    monkeypatch.setenv("AGENT_RUNNER_ID", runner_id)
    monkeypatch.setenv("ALGORITHM4_EXPERIMENT_MODE", "true")

    with TestClient(build_app()) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["runnerId"] == runner_id
        assert health.json()["llmProvider"] == "mock"
        assert health.json()["agentSlugs"] == [slug]
        experiment_state = client.get("/experiment/state")
        assert experiment_state.status_code == 200
        assert experiment_state.json() == {
            "runnerId": runner_id,
            "acceptedOffers": [],
            "activeInvocations": [],
            "completedOfferIds": [],
        }
        assert client.get(f"/agents/{slug}/health").status_code == 200
        for other_slug in other_slugs:
            assert client.get(f"/agents/{other_slug}/health").status_code == 404
