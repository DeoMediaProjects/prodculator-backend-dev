from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic_settings import PydanticBaseSettingsSource


class Settings(BaseSettings):
    # App
    APP_NAME: str = "Prodculator API"
    APP_ENV: str = "development"
    DEBUG: bool = True
    LOG_LEVEL: str = "INFO"
    FRONTEND_URL: str = "http://localhost:5173"
    BACKEND_URL: str = "http://localhost:8000"

    # Database
    DB_URL: str = "sqlite:///./prodculator.db"
    AUTO_CREATE_DB_SCHEMA: bool = True

    # JWT/Auth
    JWT_SECRET_KEY: str = "dev-secret-change-me"
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
    STRIPE_PRICE_SINGLE_USD: str = "price_1Sx84yLcLlewla5EUHVXBQY"
    STRIPE_PRICE_SINGLE_GBP: str = "price_1Sx5T8LcLlewla5EsQOLFBoy"
    STRIPE_PRICE_STUDIO_USD: str = "price_1Sx8AfLcLlewla5Exif5R15n"
    STRIPE_PRICE_STUDIO_GBP: str = "price_1Sx8CpLcLlewla5E42HQTVmg"

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

    # Script analysis chunking controls
    SCRIPT_ANALYSIS_CHUNKED_ENABLED: bool = False
    SCRIPT_CHUNK_TARGET_TOKENS: int = 1800
    SCRIPT_CHUNK_OVERLAP_TOKENS: int = 200
    SCRIPT_MAX_CHUNKS: int = 80

    # SendGrid
    SENDGRID_API_KEY: str = ""
    SENDGRID_FROM_EMAIL: str = "noreply@prodculator.com"

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

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # .env file takes priority over shell environment variables
        return (init_settings, dotenv_settings, env_settings, file_secret_settings)


@lru_cache()
def get_settings() -> Settings:
    return Settings()
