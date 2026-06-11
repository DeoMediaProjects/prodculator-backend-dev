# Prodculator API

Python FastAPI backend for the Prodculator production intelligence platform.

## Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Fill in .env with your credentials
# Required: set DB_URL in .env (example: postgresql+psycopg2://user:pass@localhost:5432/prodculator)
```

## Run

```bash
uvicorn app.main:app --reload
```

API docs available at http://localhost:8000/api/docs

### Report worker (durable queue)

Paid/B2B report generation runs on a durable Redis-backed RQ queue by default
(`REPORT_QUEUE_ENABLED=true`), so you must run a worker alongside the API:

```bash
# run at least one worker (needs the same REDIS_URL and DB_URL as the API)
python -m app.worker
```

For quick local dev without a worker, set `REPORT_QUEUE_ENABLED=false` to fall
back to in-process FastAPI BackgroundTasks — convenient, but not durable across
restarts. The test suite forces this off automatically.

## Docker

```bash
docker compose up -d --build
```

Services:
- Backend: `http://localhost:8001`
- Postgres: `localhost:5432`
- Redis: `localhost:6379`

This backend is self-contained in the `backend/` directory and can be moved to a separate workspace as-is.

## Active Module Routers

- `/api/health`
- `/api/auth/*`
- `/api/scripts/*`
- `/api/reports/*`
- `/api/payments/*`
- `/api/webhooks/stripe`
- `/api/grants`
- `/api/festivals`
- `/api/watchlist`
- `/api/subscriptions/*`
- `/api/admin/*`
- `/api/admin/email/*`

## Test

```bash
pytest
```

## Database Migrations (Alembic)

```bash
alembic revision --autogenerate -m "init"
alembic upgrade head
```
