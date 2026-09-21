from functools import lru_cache
from urllib.parse import urlparse

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic_settings import PydanticBaseSettingsSource

DEVELOPMENT_JWT_SECRET = "development-only-secret-change-before-production"


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
    # Host-header allowlist. When empty, BACKEND_URL supplies the public API
    # hostname. Platform health-check/internal hostnames can be added explicitly.
    ALLOWED_HOSTS: list[str] = []

    # Database
    DB_URL: str = "sqlite:///./prodculator.db"
    AUTO_CREATE_DB_SCHEMA: bool = True
    # Safety cap on rows returned by an unbounded query-builder read (one that sets
    # no explicit limit/range/single). Stops a runaway table from loading
    # unboundedly into memory. Generous enough that normal reference-table reads
    # never approach it; hitting it is logged as a warning (possible truncation —
    # the call site should paginate).
    DB_MAX_ROWS: int = 10000
    # Global HTTP body ceiling. The largest legitimate request is a 50 MiB
    # screenplay plus multipart overhead.
    MAX_REQUEST_BODY_BYTES: int = 55 * 1024 * 1024

    # JWT/Auth
    JWT_SECRET_KEY: str = DEVELOPMENT_JWT_SECRET  # must be overridden in production
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

    # Promotional discount, applied automatically at checkout.
    #
    # A Stripe coupon ID. This is the ONLY thing that makes a discount real: the
    # charged amount comes from the Stripe price, so a percentage written into the
    # pricing page alone would show one number and bill another. The percentage
    # below exists purely so the site can describe the coupon it is actually
    # applying, and the marketing surfaces only advertise a discount while the
    # coupon ID is set.
    STRIPE_PROMO_COUPON_ID: str = ""
    # The same offer for the one-off Single Report, as a separate coupon.
    #
    # Not a duplicate of the above by choice. The subscription offer runs for a
    # customer's first three months, which is duration=repeating, and Stripe will
    # not apply a repeating coupon to a one-time payment — one-time invoices take
    # "once" and "forever" only. Sending the subscription coupon on the report's
    # checkout would fail the session outright, so the report needs its own coupon
    # at the same percentage with duration=once.
    #
    # Left blank, the report is not discounted AND is not advertised as discounted.
    STRIPE_PROMO_ONEOFF_COUPON_ID: str = ""
    STRIPE_PROMO_PERCENT_OFF: int = 0
    STRIPE_PROMO_LABEL: str = ""
    # Which plans the coupon actually covers, comma separated. A Stripe coupon is
    # scoped to specific products, and applying it to a checkout for a product it
    # does not cover makes Stripe reject the session — which would break the
    # purchase entirely, not merely fail to discount it. It is also what stops the
    # pricing page advertising a saving on a plan that will be charged in full.
    #
    # The launch offer covers every paid product: all three subscription plans and
    # the one-off Single Report. Explorer is free and has nothing to discount.
    #
    # "single" is the one-off report. It is not a subscription plan and has no place
    # in the plan hierarchy, but it is inside this coupon, so it needs a key here for
    # the credit checkout to send the discount and the pricing cards to show it.
    #
    # This value must stay equal to the coupon's product scope in Stripe: widening
    # it here without widening it there makes Stripe reject those checkouts
    # outright, and narrowing it there without narrowing it here puts a discount on
    # a plan billed in full.
    STRIPE_PROMO_PLANS: str = "professional,producer,studio,single"
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

    # ── Compressed-cycle billing test (LIVE money) ────────────────────────────
    # A controlled way to validate the recurring-charge machinery without
    # charging a real plan price. When enabled, admins can mint a Checkout that
    # bills a token amount on the normal monthly cycle and auto-refunds every
    # charge so the test subscriber is kept whole. It is the amount and the
    # refund that make this safe, not the cadence: the cadence deliberately
    # matches production so the subscription behaves the way a tester expects.
    # OFF by default — while false, the test endpoints
    # 404 AND the auto-refund webhook path is completely dormant (cannot refund
    # anything). A refund fires only for subscriptions explicitly tagged
    # metadata.autoRefund="true", so a real customer's invoice is never touched.
    STRIPE_TEST_BILLING_ENABLED: bool = False
    # Renewal cadence for the test price, in days (Stripe interval=day).
    # 30 days mirrors the real monthly subscription so testers see the same
    # period length and renewal date they would in production. The token amount
    # and immediate auto-refund below are what keep the test cheap; compressing
    # the cycle as well made the subscription itself behave unrealistically.
    STRIPE_TEST_BILLING_INTERVAL_DAYS: int = 30
    # Amount the test price charges, in the currency's minor unit (100 = $1/£1).
    # Deliberately a token amount so real prices stay untouched and the residual
    # Stripe processing fee is negligible. Bump to test the real amount instead.
    STRIPE_TEST_BILLING_UNIT_AMOUNT: int = 100

    # Anthropic Claude
    ANTHROPIC_API_KEY: str = ""
    # Default to Sonnet, not Opus. Report generation makes many calls per report
    # (one per script chunk + aggregation + the narrative), so Opus (~5x Sonnet's
    # price) made a single report cost several dollars and drove a large,
    # unexpected bill. Sonnet 4.6 is the right cost/quality balance for this
    # pipeline. Set ANTHROPIC_MODEL=claude-opus-4-8 in the env only if a specific
    # report genuinely needs Opus-level quality.
    ANTHROPIC_MODEL: str = "claude-sonnet-4-6"
    # Default output cap for stages without their own budget. Note this is NOT
    # sufficient for the report narrative, which sets its own higher budget below.
    ANTHROPIC_MAX_TOKENS: int = 8000
    ANTHROPIC_ANALYSIS_TIMEOUT: int = 120
    ANTHROPIC_MAX_TOKENS_SCRIPT_CHUNK: int | None = None
    ANTHROPIC_MAX_TOKENS_SCRIPT_AGGREGATE: int | None = None
    # The narrative fill writes prose for every ranked territory, so its output
    # scales with territory count and is the largest generation in the pipeline.
    # Left unset it inherited ANTHROPIC_MAX_TOKENS (8000) and was truncated
    # mid-JSON on a real four-territory report, which failed the parse and shipped
    # the report with "AI narrative generation unavailable" while still billing
    # the user. The timeout comment below already assumed a ~12k-token generation;
    # this makes the token budget agree with that, with headroom.
    ANTHROPIC_MAX_TOKENS_REPORT: int | None = 16000
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

    # OpenAI — same-quality fallback for script/report generation when Anthropic
    # is unreachable or out of credits. Used with the IDENTICAL prompts and
    # per-stage token budgets as the Anthropic path (see ScriptAnalysisService);
    # this is not a degraded/heuristic fallback, it's the same instructions on a
    # different model. Blank key means the fallback is simply skipped and the
    # existing fail-closed (refund) behavior applies, exactly as before.
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o"

    # Google Gemini — a second same-quality fallback, sitting behind OpenAI in
    # the chain by default. Identical role to the OpenAI path: the SAME prompts
    # and per-stage token budgets on a different model, not a degraded path.
    # Blank key means Gemini is skipped, exactly like a blank OpenAI key.
    GEMINI_API_KEY: str = ""
    # Sonnet-tier default, picked to match what ANTHROPIC_MODEL is for: a stable
    # workhorse, not the most expensive model available. Gemini 3.8 Flash is the
    # current stable Flash release and is Google's closest analogue to Claude
    # Sonnet in the cost/quality tradeoff this pipeline needs (many calls per
    # report). Set GEMINI_MODEL=gemini-3.1-pro-preview only if a report genuinely
    # needs Pro-level quality — same reasoning as the Opus note above.
    GEMINI_MODEL: str = "gemini-3.8-flash"
    # Gemini 3.x models always think before answering and CANNOT have thinking
    # turned off; "low" is the floor. This matters here because thought tokens
    # are billed against max_output_tokens (Google's documented behaviour), so
    # left at the default level a 1500-token script-chunk budget can be spent
    # entirely on thinking and return an empty/truncated body. Set to "" to send
    # no thinking config at all (e.g. on a model or SDK build that rejects it).
    GEMINI_THINKING_LEVEL: str = "low"
    # Headroom multiplier applied to the per-stage max-tokens budget on the
    # Gemini path, for the same reason: the Anthropic budgets were sized for
    # answer tokens only, and Gemini spends part of the same allowance on
    # thought tokens. 2.0 keeps the answer budget intact with room to think.
    GEMINI_MAX_TOKENS_MULTIPLIER: float = 2.0

    # Order the fallback providers are tried in after Anthropic fails, as a
    # comma-separated list of "openai" and "gemini". Unconfigured providers are
    # skipped, so the default is safe with either key missing. Narrow this (e.g.
    # LLM_FALLBACK_PROVIDERS=gemini) to skip a provider whose key is still in the
    # env but is known to be out of credits — otherwise every fallback pays that
    # provider's full retry schedule before moving on.
    LLM_FALLBACK_PROVIDERS: str = "openai,gemini"

    # v2 report orchestration. When on, the builder additionally assembles the
    # canonical 13-section payload alongside the legacy report and attaches it
    # for comparison. Nothing renders from it: the flag exists so the
    # old-versus-v2 diff can be run against real report runs before any cutover,
    # which is the one thing that cannot be rehearsed with fixtures.
    #
    # Default off. A half-wired orchestration reaching a paid report is the
    # failure this whole sequence is designed to prevent.
    REPORT_ORCHESTRATION_V2_ENABLED: bool = False

    # Script analysis chunking controls.
    # `SCRIPT_ANALYSIS_CHUNKED_ENABLED` was removed: the legacy hard-trim path
    # it selected away from is gone, so chunking is the only path and the flag
    # decided nothing. Deployed environments that still set it are unaffected —
    # `extra="ignore"` below means an unrecognised key is dropped rather than
    # rejected — so the variable can be deleted from Railway at leisure.
    SCRIPT_CHUNK_TARGET_TOKENS: int = 1800
    SCRIPT_CHUNK_OVERLAP_TOKENS: int = 200
    SCRIPT_MAX_CHUNKS: int = 80

    # Brevo (transactional email)
    BREVO_API_KEY: str = ""
    BREVO_FROM_EMAIL: str = "noreply@prodculator.com"
    BREVO_FROM_NAME: str = "Prodculator"
    CONTACT_EMAIL: str = "support@prodculator.com"
    # Ops recipient for Business Intelligence operational alerts (a scheduled
    # delivery held for insufficient data, a generation/delivery failure).
    # Falls back to CONTACT_EMAIL when blank.
    B2B_ADMIN_ALERT_EMAIL: str = ""

    # ── Operational alerts (handoff §4.5) ────────────────────────────────────
    # Ops recipient for every failure alert: BI generation, B2C report
    # generation, Stripe webhook processing, and scheduled jobs. Falls back to
    # B2B_ADMIN_ALERT_EMAIL then CONTACT_EMAIL, so an existing deployment keeps
    # routing BI alerts where it already did.
    ADMIN_ALERT_EMAIL: str = ""
    # Per-alert-key quiet window. The first failure in a window emails
    # immediately; further occurrences are counted and reported in the next
    # send, so a systemic outage cannot emit thousands of emails. Payment
    # webhook alerts bypass this (see alerts.NEVER_THROTTLED) because each one
    # names a different charged customer.
    ADMIN_ALERT_THROTTLE_SECONDS: int = 900

    # ── Admin audit trail (handoff §4.4/§4.5) ────────────────────────────────
    # How long admin_audit_logs rows are kept before the daily retention job
    # deletes them. 730 days (two years) covers an annual review cycle plus the
    # year it audits, which is the horizon that matters for a service handling
    # payment and personal data. 0 or less means retain indefinitely.
    ADMIN_AUDIT_RETENTION_DAYS: int = 730

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

    @property
    def is_production(self) -> bool:
        return self.APP_ENV.strip().lower() in {"prod", "production", "release"}

    @property
    def trusted_hosts(self) -> list[str]:
        hosts = [host.strip() for host in self.ALLOWED_HOSTS if host.strip()]
        backend_host = urlparse(self.BACKEND_URL).hostname
        if backend_host and backend_host not in hosts:
            hosts.append(backend_host)
        if not self.is_production:
            for host in ("testserver", "localhost", "127.0.0.1"):
                if host not in hosts:
                    hosts.append(host)
        return hosts

    @model_validator(mode="after")
    def validate_production_safety(self) -> "Settings":
        """Refuse to boot production with development-grade defaults."""
        if not self.is_production:
            return self

        problems: list[str] = []
        if self.DEBUG:
            problems.append("DEBUG must be false")
        if self.JWT_SECRET_KEY == DEVELOPMENT_JWT_SECRET or len(self.JWT_SECRET_KEY) < 48:
            problems.append("JWT_SECRET_KEY must be a unique secret of at least 48 characters")
        if not self.AUTH_COOKIE_ENABLED or not self.AUTH_COOKIE_SECURE:
            problems.append("secure cookie authentication must be enabled")
        if self.AUTO_CREATE_DB_SCHEMA:
            problems.append("AUTO_CREATE_DB_SCHEMA must be false; run Alembic migrations explicitly")
        if self.DB_URL.lower().startswith("sqlite"):
            problems.append("DB_URL must use a production database, not SQLite")
        if not self.RATE_LIMIT_ENABLED or self.rate_limit_storage_uri == "memory://":
            problems.append("distributed rate limiting must be enabled with Redis storage")
        if not 1024 * 1024 <= self.MAX_REQUEST_BODY_BYTES <= 100 * 1024 * 1024:
            problems.append("MAX_REQUEST_BODY_BYTES must be between 1 MiB and 100 MiB")

        for name, value in (("FRONTEND_URL", self.FRONTEND_URL), ("BACKEND_URL", self.BACKEND_URL)):
            parsed = urlparse(value)
            if parsed.scheme != "https" or not parsed.hostname:
                problems.append(f"{name} must be an absolute HTTPS URL")

        if not self.CORS_ORIGINS:
            problems.append("CORS_ORIGINS must contain at least one trusted frontend origin")
        for origin in self.CORS_ORIGINS:
            parsed = urlparse(origin)
            if origin == "*" or parsed.scheme != "https" or not parsed.hostname:
                problems.append(f"unsafe CORS origin: {origin!r}")
        if any(host == "*" for host in self.trusted_hosts):
            problems.append("ALLOWED_HOSTS cannot contain '*' in production")

        s3_values = (
            self.AWS_S3_BUCKET_NAME,
            self.AWS_ACCESS_KEY_ID,
            self.AWS_SECRET_ACCESS_KEY,
        )
        if not all(s3_values):
            problems.append("durable S3 storage credentials and bucket are required")

        if self.STRIPE_SECRET_KEY:
            from app.modules.payments.plan_catalog import find_missing_price_ids

            missing_prices = find_missing_price_ids(self)
            if missing_prices:
                problems.append("missing Stripe price IDs: " + ", ".join(missing_prices))

        if problems:
            raise ValueError("Unsafe production configuration: " + "; ".join(problems))
        return self

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
