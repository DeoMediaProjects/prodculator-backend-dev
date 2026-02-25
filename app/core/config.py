from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # App
    APP_NAME: str = "Prodculator API"
    APP_ENV: str = "development"
    DEBUG: bool = True
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

    # Local object storage
    STORAGE_ROOT: str = "./storage"

    # Stripe
    STRIPE_SECRET_KEY: str = ""
    STRIPE_PUBLISHABLE_KEY: str = ""
    STRIPE_WEBHOOK_SECRET: str = ""
    STRIPE_PRICE_SINGLE_USD: str = "price_1Sx84yLcLlewla5EUHVXBQY"
    STRIPE_PRICE_SINGLE_GBP: str = "price_1Sx5T8LcLlewla5EsQOLFBoy"
    STRIPE_PRICE_STUDIO_USD: str = "price_1Sx8AfLcLlewla5Exif5R15n"
    STRIPE_PRICE_STUDIO_GBP: str = "price_1Sx8CpLcLlewla5E42HQTVmg"

    # OpenAI
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o"
    OPENAI_MAX_TOKENS: int = 2000

    # SendGrid
    SENDGRID_API_KEY: str = ""
    SENDGRID_FROM_EMAIL: str = "noreply@prodculator.com"

    # Google Maps
    GOOGLE_MAPS_API_KEY: str = ""

    # TMDB
    TMDB_API_KEY: str = ""

    # Exchange Rate
    EXCHANGE_RATE_API_KEY: str = ""

    # BLS
    BLS_API_KEY: str = ""

    # Grantify
    GRANTIFY_API_KEY: str = ""
    GRANTIFY_AFFILIATE_ID: str = ""

    # CORS
    CORS_ORIGINS: list[str] = ["http://localhost:5173"]

    model_config = {
        "env_file": ".env",
        "case_sensitive": True,
        "extra": "ignore",
    }


@lru_cache()
def get_settings() -> Settings:
    return Settings()
