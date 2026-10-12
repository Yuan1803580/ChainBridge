from __future__ import annotations


def test_health_exposes_local_runtime_identity(client, monkeypatch):
    monkeypatch.setenv("LOCAL_PLATFORM_RUNTIME_ID", "runtime-test-123")

    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json()["service"] == "agent-crosschain-platform-api"
    assert response.json()["runtimeId"] == "runtime-test-123"

