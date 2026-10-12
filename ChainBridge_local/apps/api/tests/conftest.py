from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture(autouse=True)
def test_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("REQUIRE_SIGNED_AGENT_RESULTS", "false")
    monkeypatch.setenv("AUTO_INVOKE_AGENTS", "false")
    monkeypatch.setenv("AGENT_RUNTIME_TOKEN_STORE_PATH", str(tmp_path / "agent-runtime-tokens.json"))


@pytest.fixture()
def client():
    application = create_app(
        Settings(
            app_env="test",
            app_secret="test-only-secret-that-is-not-used-outside-pytest",
            database_url="sqlite://",
            cors_origins=("http://localhost:3000",),
            jwt_ttl_seconds=3600,
            challenge_ttl_seconds=300,
        )
    )
    with TestClient(application) as test_client:
        yield test_client


def dev_login(client: TestClient, slug: str) -> dict:
    response = client.post("/api/v1/auth/dev-login", json={"did": f"did:local:{slug}"})
    assert response.status_code == 200, response.text
    return response.json()


def auth_headers(client: TestClient, slug: str, key: str | None = None) -> dict[str, str]:
    token = dev_login(client, slug)["accessToken"]
    headers = {"Authorization": f"Bearer {token}"}
    if key:
        headers["Idempotency-Key"] = key
    return headers
