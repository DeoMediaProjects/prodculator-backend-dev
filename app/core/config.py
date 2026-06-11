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
    FRONTEND_URL: str = "http://localhost:5173"
    BACKEND_URL: str = "http://localhost:8000"

    # Database
    DB_URL: str = "sqlite:///./prodculator.db"
    AUTO_CREATE_DB_SCHEMA: bool = True

    # JWT/Auth
    JWT_SECRET_KEY: str = "dev-secret-change-me"  # must be overridden in production
    JWT_ACCESS_TOKEN_EXPIRES_SECONDS: int = 3600
    JWT_REFRESH_TOKEN_EXPIRES_SECONDS: int = 1209600

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

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

    # Anthropic Claude
    ANTHROPIC_API_KEY: str = ""
    ANTHROPIC_MODEL: str = "claude-3-5-sonnet-20241022"
    ANTHROPIC_MAX_TOKENS: int = 12000
    ANTHROPIC_ANALYSIS_TIMEOUT: int = 120
    ANTHROPIC_MAX_TOKENS_SCRIPT_CHUNK: int | None = None
    ANTHROPIC_MAX_TOKENS_SCRIPT_AGGREGATE: int | None = None
    ANTHROPIC_MAX_TOKENS_REPORT: int | None = None
    ANTHROPIC_TIMEOUT_SCRIPT_CHUNK: int | None = 180
    ANTHROPIC_TIMEOUT_SCRIPT_AGGREGATE: int | None = None
    ANTHROPIC_TIMEOUT_REPORT: int | None = None
    # Short timeout for the pre-flight reachability probe run before a report is
    # charged. Kept low so a Claude outage fails fast instead of hanging the request.
    ANTHROPIC_HEALTHCHECK_TIMEOUT: int = 10

    # Script analysis chunking controls
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
