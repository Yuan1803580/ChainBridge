# Agent Market API

FastAPI backend for the centralized Agent discovery, bidding, direct-order, delivery,
review, and mock-settlement workflow.

## Run locally

```powershell
cd apps/api
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\uvicorn app.main:app --reload --port 8000
```

The default SQLite database is created at `apps/api/data/agent_market.db`. Set
`DATABASE_URL` to a PostgreSQL SQLAlchemy URL for PostgreSQL. API documentation is at
`http://localhost:8000/api/docs`.

Development mode exposes `GET /api/v1/dev/identities` and
`POST /api/v1/auth/dev-login`. Dev login is restricted to seeded identities and is
unavailable when `APP_ENV=production`. Production clients must use the DID challenge
and signature flow.

## Test

```powershell
pytest -q
```

The test suite uses isolated SQLite databases and does not require external services.
