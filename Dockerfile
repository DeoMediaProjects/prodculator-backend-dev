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
RUN python -m pip install --no-cache-dir --upgrade "pip>=26.2" \
    && python -m pip install --no-cache-dir -r requirements.txt

COPY app ./app
# Operational scripts (demo-account seeding, backfills, reconciliation) are run
# by hand from the Railway console, so they must exist inside the image — being
# in the repo is not enough.
COPY scripts ./scripts
# Reviewed handoff snapshots are staged by an explicit, non-destructive import
# command. They are never read by the paid report matcher directly.
COPY data/handoff_snapshots ./data/handoff_snapshots
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

# Run the application without root privileges. The local storage directory stays
# writable for development; production should use S3.
RUN groupadd --gid 10001 prodculator \
    && useradd --uid 10001 --gid prodculator --no-create-home --shell /usr/sbin/nologin prodculator \
    && mkdir -p /app/storage \
    && chown -R prodculator:prodculator /app

USER prodculator

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)" || exit 1

# Proxy headers are enabled below, but only explicitly trusted proxy addresses
# may supply them (configured through UVICORN_FORWARDED_ALLOW_IPS).
# Configure UVICORN_FORWARDED_ALLOW_IPS with the exact reverse-proxy CIDR(s).
# Trusting "*" lets direct clients spoof their IP and evade per-IP rate limits.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
