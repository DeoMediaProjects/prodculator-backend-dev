from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic_settings import PydanticBaseSettingsSource


class Settings(BaseSettings):
    # App
    APP_NAME: str = "Prodculator API"
    APP_ENV: str = "development"
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"
    # Emit logs as structured JSON (one object per line) so they're queryable in a
    # log aggregator and carry the per-request X-Request-ID. Leave false for
    # human-readable text in local dev; set true in production.
    LOG_JSON: bool = False

    # Error monitoring (Sentry). No-op when SENTRY_DSN is empty, so it's safe to
    # leave unset in dev. SENTRY_TRACES_SAMPLE_RATE controls performance tracing
    # (0.0 = errors only). SENTRY_ENVIRONMENT defaults to APP_ENV when unset.
    SENTRY_DSN: str = ""
    SENTRY_TRACES_SAMPLE_RATE: float = 0.0
    SENTRY_ENVIRONMENT: str | None = None
    FRONTEND_URL: str = "http://localhost:5173"
    BACKEND_URL: str = "http://localhost:8000"

    # Database
    DB_URL: str = "sqlite:///./prodculator.db"
    AUTO_CREATE_DB_SCHEMA: bool = True
    # Safety cap on rows returned by an unbounded query-builder read (one that sets
    # no explicit limit/range/single). Stops a runaway table from loading
    # unboundedly into memory. Generous enough that normal reference-table reads
    # never approach it; hitting it is logged as a warning (possible truncation —
    # the call site should paginate).
    DB_MAX_ROWS: int = 10000

    # JWT/Auth
    JWT_SECRET_KEY: str = "dev-secret-change-me"  # must be overridden in production
    JWT_ACCESS_TOKEN_EXPIRES_SECONDS: int = 3600
    JWT_REFRESH_TOKEN_EXPIRES_SECONDS: int = 1209600

    # Cookie-based auth. When enabled, sign-in/refresh issue the JWT pair as
    # httpOnly cookies (not readable by JavaScript) so the browser never stores
    # tokens in localStorage — closing off token theft via XSS. The Bearer header
    # is still accepted (for API clients and the test suite), so this is additive.
    # AUTH_COOKIE_SECURE must be true in production (HTTPS); set false only for
    # local http dev. SAMESITE "lax" is the safe default for a same-site SPA.
    AUTH_COOKIE_ENABLED: bool = True
    AUTH_COOKIE_SECURE: bool = True
    AUTH_COOKIE_SAMESITE: str = "lax"  # "lax" | "strict" | "none"
    AUTH_COOKIE_DOMAIN: str | None = None  # e.g. ".prodculator.com" to share across subdomains

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # Rate limiting (SlowAPI). Counters are stored in Redis so limits are shared
    # across web workers and survive restarts — an in-memory store would make the
    # limit effectively `workers × configured` and reset on every deploy. Leave
    # RATE_LIMIT_STORAGE_URI empty to reuse REDIS_URL; set it to "memory://" for
    # single-process local use or tests. RATE_LIMIT_ENABLED=false disables limits.
    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_STORAGE_URI: str = ""

    @property
    def rate_limit_storage_uri(self) -> str:
        """Effective SlowAPI storage backend (defaults to the shared Redis)."""
        return self.RATE_LIMIT_STORAGE_URI or self.REDIS_URL

    # Durable background-job queue (RQ over Redis). Enabled by default so prod is
    # safe-by-default: paid/b2b report generation is enqueued onto Redis and
    # processed by a separate worker (`python -m app.worker`) — surviving
    # web-process restarts. This requires a worker to be running. For quick local
    # dev WITHOUT a worker, set this false to fall back to in-process FastAPI
    # BackgroundTasks (the test suite forces it off in conftest).
    REPORT_QUEUE_ENABLED: bool = True
    REPORT_QUEUE_JOB_TIMEOUT: int = 1800  # 30 min — generous upper bound for a full report

    # Local object storage (dev fallback when AWS creds are not set)
    STORAGE_ROOT: str = "./storage"

    # AWS S3
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""
    AWS_S3_REGION: str = "eu-west-1"
    AWS_S3_BUCKET_NAME: str = ""
    AWS_S3_REPORTS_PREFIX: str = "reports"
    AWS_S3_PRESIGNED_URL_EXPIRY: int = 900  # 15 minutes — generated fresh on every request

    # Stripe
    STRIPE_SECRET_KEY: str = ""
    STRIPE_PUBLISHABLE_KEY: str = ""
    STRIPE_WEBHOOK_SECRET: str = ""
    # Legacy one-time / pay-per-report prices
    STRIPE_PRICE_SINGLE_USD: str = ""
    STRIPE_PRICE_SINGLE_GBP: str = ""
    # Professional monthly
    STRIPE_PRICE_PROFESSIONAL_USD: str = ""
    STRIPE_PRICE_PROFESSIONAL_GBP: str = ""
    # Producer monthly
    STRIPE_PRICE_PRODUCER_USD: str = ""
    STRIPE_PRICE_PRODUCER_GBP: str = ""
    # Studio monthly
    STRIPE_PRICE_STUDIO_USD: str = ""
    STRIPE_PRICE_STUDIO_GBP: str = ""
    # Annual billing — GBP
    STRIPE_PRICE_PROFESSIONAL_ANNUAL_GBP: str = ""
    STRIPE_PRICE_PROFESSIONAL_ANNUAL_USD: str = ""
    STRIPE_PRICE_PRODUCER_ANNUAL_GBP: str = ""
    STRIPE_PRICE_PRODUCER_ANNUAL_USD: str = ""
    STRIPE_PRICE_STUDIO_ANNUAL_GBP: str = ""
    STRIPE_PRICE_STUDIO_ANNUAL_USD: str = ""
    # B2B monthly subscriptions
    STRIPE_PRICE_B2B_CAMERA_EQUIPMENT_GBP: str = ""
    STRIPE_PRICE_B2B_CAMERA_EQUIPMENT_USD: str = ""
    STRIPE_PRICE_B2B_PRODUCTION_SERVICES_GBP: str = ""
    STRIPE_PRICE_B2B_PRODUCTION_SERVICES_USD: str = ""
    STRIPE_PRICE_B2B_CREW_CASTING_GBP: str = ""
    STRIPE_PRICE_B2B_CREW_CASTING_USD: str = ""
    STRIPE_PRICE_B2B_PRODUCTION_TREND_GBP: str = ""
    STRIPE_PRICE_B2B_PRODUCTION_TREND_USD: str = ""

    # Anthropic Claude
    ANTHROPIC_API_KEY: str = ""
    # Default to Sonnet, not Opus. Report generation makes many calls per report
    # (one per script chunk + aggregation + the narrative), so Opus (~5x Sonnet's
    # price) made a single report cost several dollars and drove a large,
    # unexpected bill. Sonnet 4.6 is the right cost/quality balance for this
    # pipeline. Set ANTHROPIC_MODEL=claude-opus-4-8 in the env only if a specific
    # report genuinely needs Opus-level quality.
    ANTHROPIC_MODEL: str = "claude-sonnet-4-6"
    # Output cap. Lower than before (was 12000) to bound per-call output cost;
    # the report JSON comfortably fits under this.
    ANTHROPIC_MAX_TOKENS: int = 8000
    ANTHROPIC_ANALYSIS_TIMEOUT: int = 120
    ANTHROPIC_MAX_TOKENS_SCRIPT_CHUNK: int | None = None
    ANTHROPIC_MAX_TOKENS_SCRIPT_AGGREGATE: int | None = None
    ANTHROPIC_MAX_TOKENS_REPORT: int | None = None
    ANTHROPIC_TIMEOUT_SCRIPT_CHUNK: int | None = 180
    ANTHROPIC_TIMEOUT_SCRIPT_AGGREGATE: int | None = None
    # The report narrative is a large (up to 12k-token) generation on a slow
    # model and runs in a background worker, so it gets a generous timeout. It is
    # also STREAMED (see _call_anthropic_with_retry) which resets the read
    # timeout per chunk — the 120s default caused every attempt to time out.
    ANTHROPIC_TIMEOUT_REPORT: int | None = 600
    # Short timeout for the pre-flight reachability probe run before a report is
    # charged. Kept low so a Claude outage fails fast instead of hanging the request.
    ANTHROPIC_HEALTHCHECK_TIMEOUT: int = 10

    # Script analysis chunking controls.
    SCRIPT_ANALYSIS_CHUNKED_ENABLED: bool = False
    SCRIPT_CHUNK_TARGET_TOKENS: int = 1800
    SCRIPT_CHUNK_OVERLAP_TOKENS: int = 200
    SCRIPT_MAX_CHUNKS: int = 80

    # Brevo (transactional email)
    BREVO_API_KEY: str = ""
    BREVO_FROM_EMAIL: str = "noreply@prodculator.com"
    BREVO_FROM_NAME: str = "Prodculator"
    CONTACT_EMAIL: str = "support@prodculator.com"

    # Firebase / Google Auth
    FIREBASE_PROJECT_ID: str = ""
    FIREBASE_SERVICE_ACCOUNT_JSON: str = ""  # path to JSON file or inline JSON string

    # Google Maps
    GOOGLE_MAPS_API_KEY: str = ""

    # TMDB
    TMDB_API_KEY: str = ""

    # Exchange Rate
    EXCHANGE_RATE_API_KEY: str = ""

    # BLS
    BLS_API_KEY: str = ""

    # FRED
    FRED_API_KEY: str = ""

    # Grantify
    GRANTIFY_API_KEY: str = ""
    GRANTIFY_AFFILIATE_ID: str = ""

    # Scraper
    SCRAPER_ENABLED: bool = True
    # Background scheduler (APScheduler). When running multiple web workers, a
    # Postgres advisory lock ensures only ONE worker actually runs the jobs; set
    # this to false to fully opt a process out (e.g. if you run a dedicated
    # scheduler process).
    SCHEDULER_ENABLED: bool = True
    SCRAPER_REQUEST_TIMEOUT: int = 30
    SCRAPER_MAX_TEXT_CHARS: int = 60000

    # CORS
    CORS_ORIGINS: list[str] = ["http://localhost:5173"]

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    @field_validator("JWT_SECRET_KEY")
    @classmethod
    def validate_jwt_secret(cls, v: str) -> str:
        if len(v) < 32:
            raise ValueError("JWT_SECRET_KEY must be at least 32 characters")
        return v

    @field_validator("DEBUG", mode="before")
    @classmethod
    def normalize_debug(cls, v: object) -> object:
        if isinstance(v, str):
            value = v.strip().lower()
            if value in {"release", "prod", "production"}:
                return False
            if value in {"dev", "development"}:
                return True
        return v

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # env vars > .env file > defaults  (12-Factor compliant)
        return (init_settings, env_settings, dotenv_settings, file_secret_settings)


@lru_cache()
def get_settings() -> Settings:
    return Settings()
