from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    api_key: str = "dev-api-key"
    database_url: str = "postgresql+asyncpg://payment:payment@localhost:5432/payment"
    rabbitmq_url: str = "amqp://guest:guest@localhost:5672/"
    webhook_secret: str = "dev-webhook-secret"
    outbox_relay_enabled: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()
