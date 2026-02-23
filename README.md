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

## Docker

```bash
docker build -t prodculator-api .
docker run --rm -p 8000:8000 --env-file .env prodculator-api
```

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
