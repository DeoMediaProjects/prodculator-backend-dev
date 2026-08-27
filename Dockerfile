FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libcairo2 \
    libffi-dev \
    libgdk-pixbuf-2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
# Operational scripts (demo-account seeding, backfills, reconciliation) are run
# by hand from the Railway console, so they must exist inside the image — being
# in the repo is not enough.
COPY scripts ./scripts
# Migrations, for the same reason as scripts above: `alembic upgrade head` needs
# the version files and the config, not just the alembic package from
# requirements.txt. Without these the Railway console could not migrate at all,
# so a production migration meant pointing a local shell at DATABASE_PUBLIC_URL
# and hand-feeding the URL in — easy to aim at the wrong database, and it bypasses
# the one environment that already has the right DB_URL.
#
# alembic.ini uses `script_location = alembic` and `prepend_sys_path = .`, both
# relative, so both paths must land in WORKDIR. Its `sqlalchemy.url` is a sqlite
# placeholder and is overridden by env.py from settings.DB_URL.
#
# The version files import from app.alembic_utils, app.core.{audit_notes,config,
# territories} and app.models.sql_models — all inside `app`, copied above.
COPY alembic.ini ./alembic.ini
COPY alembic ./alembic
COPY .env.example ./.env.example
COPY README.md ./README.md

EXPOSE 8000

# --proxy-headers + --forwarded-allow-ips so request.client.host reflects the
# real client IP behind a reverse proxy. Without this, per-client rate limiting
# would bucket every request under the proxy's IP.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
