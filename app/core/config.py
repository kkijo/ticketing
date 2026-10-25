from pydantic import PostgresDsn, RedisDsn, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    ENV: str = "local"
    DEBUG: bool = True

    DATABASE_URL: PostgresDsn
    REDIS_URL: RedisDsn

    JWT_SECRET: SecretStr
    JWT_ALG: str = "HS256"
    ACCESS_TTL_SEC: int = 900
    REFRESH_TTL_SEC: int = 60 * 60 * 24 * 30

    HOLD_TTL_SEC: int = 600

    STRIPE_SECRET_KEY: SecretStr | None = None
    STRIPE_WEBHOOK_SECRET: SecretStr | None = None


settings = Settings()  # type: ignore[call-arg]