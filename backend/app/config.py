from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    APP_ENV: Literal["local", "dev", "staging", "prod"] = "local"
    LOG_LEVEL: str = "INFO"
    DATABASE_URL: str


settings = Settings()  # pyright: ignore[reportCallIssue]
