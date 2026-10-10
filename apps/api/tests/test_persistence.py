from __future__ import annotations

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

from .conftest import auth_headers


def test_sqlite_persistence_across_app_restart(tmp_path):
    database = tmp_path / "market.db"
    settings = Settings(
        app_env="test",
        app_secret="persistence-test-secret-with-at-least-32-bytes",
        database_url=f"sqlite:///{database}",
    )
    first_app = create_app(settings)
    with TestClient(first_app) as first:
        response = first.post(
            "/api/v1/demands",
            headers=auth_headers(first, "campus-requester", "persist-demand-1"),
            json={
                "title": "需要持久化的需求",
                "description": "该需求在应用重启后仍应存在。",
                "inputPayload": {},
                "acceptanceCriteria": "重启后可查询。",
                "budget": "2.00",
                "currency": "USDC",
                "status": "published",
            },
        )
        assert response.status_code == 201
        demand_id = response.json()["id"]

    second_app = create_app(settings)
    with TestClient(second_app) as second:
        response = second.get(f"/api/v1/demands/{demand_id}")
        assert response.status_code == 200
        assert response.json()["title"] == "需要持久化的需求"
