"""Application settings, driven entirely by environment variables.

Never hardcode secrets. Every value here comes from the environment (or a
local .env file for development only).
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- App -------------------------------------------------------------
    APP_NAME: str = "Smart Dealer Enquiry API"
    API_V1_PREFIX: str = "/api/v1"
    ENVIRONMENT: str = "development"
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"

    # Comma-separated list of allowed origins for CORS. The Flutter web dev
    # server and localhost defaults are included for convenience.
    CORS_ORIGINS: str = "http://localhost:3000,http://localhost:8080,http://127.0.0.1:8080"

    # --- Database --------------------------------------------------------
    DATABASE_URL: str = "postgresql+asyncpg://dealerapp:dealerapp@localhost:5432/dealerapp"
    DB_ECHO: bool = False
    DB_POOL_SIZE: int = 5
    DB_MAX_OVERFLOW: int = 10

    # --- AWS -------------------------------------------------------------
    AWS_REGION: str = "ap-south-1"

    # Cognito
    COGNITO_USER_POOL_ID: str = ""
    COGNITO_APP_CLIENT_ID: str = ""

    # S3
    S3_BUCKET: str = ""
    S3_PRESIGN_EXPIRY_SECONDS: int = 900

    # SES / SNS
    SES_FROM_EMAIL: str = ""
    SNS_SMS_ENABLED: bool = False

    # Bedrock
    BEDROCK_MODEL_ID: str = ""
    BEDROCK_MAX_TOKENS: int = 512
    BEDROCK_ENABLED: bool = True

    # --- Internal endpoints ---------------------------------------------
    # Shared secret guarding /internal/* endpoints (OBD ingest, incentive
    # recompute). These are demo/ops hooks, not public API surface.
    INTERNAL_API_KEY: str = Field(default="dev-internal-key")

    # --- Auth behaviour --------------------------------------------------
    # Verify the "aud" (ID token) / "client_id" (access token) claim against
    # COGNITO_APP_CLIENT_ID. Only disable for local testing.
    VERIFY_TOKEN_AUDIENCE: bool = True
    JWKS_CACHE_SECONDS: int = 3600

    # Local-only escape hatch: skip Cognito signature verification and trust a
    # dev header. Refuses to activate unless ENVIRONMENT == "development".
    AUTH_DEV_MODE: bool = False

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def cognito_issuer(self) -> str:
        return (
            f"https://cognito-idp.{self.AWS_REGION}.amazonaws.com/"
            f"{self.COGNITO_USER_POOL_ID}"
        )

    @property
    def cognito_jwks_url(self) -> str:
        return f"{self.cognito_issuer}/.well-known/jwks.json"

    @property
    def dev_auth_enabled(self) -> bool:
        return self.AUTH_DEV_MODE and self.ENVIRONMENT == "development"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
