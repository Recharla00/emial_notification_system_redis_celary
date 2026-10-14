from functools import lru_cache
from typing import Literal

from pydantic import EmailStr, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    api_key: SecretStr = Field(min_length=24)
    database_url: str = "sqlite:///./notifications.db"
    redis_url: str = "redis://localhost:6379/0"
    smtp_host: str = "localhost"
    smtp_port: int = Field(default=1025, ge=1, le=65535)
    smtp_username: str = ""
    smtp_password: SecretStr = SecretStr("")
    smtp_security: Literal["none", "starttls", "ssl"] = "none"
    smtp_from: EmailStr = "notifications@example.com"
    max_attempts: int = Field(default=5, ge=1, le=20)
    app_log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
