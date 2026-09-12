from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    app_env: str = "development"
    app_debug: bool = True
    app_host: str = "0.0.0.0"
    app_port: int = Field(default=8000, ge=1, le=65535)
    api_v1_prefix: str = "/api/v1"

    database_url: str = "postgresql+psycopg://urban:urban@localhost:5432/urban_generator"
    redis_url: str = "redis://localhost:6379/0"
    storage_root: Path = Path("./storage")
    max_upload_size_mb: int = Field(default=1024, ge=1, le=10240)
    cors_origins: str = "http://localhost:5173"

    @field_validator("database_url", "redis_url", "api_v1_prefix")
    @classmethod
    def validate_non_empty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value must not be empty")
        return value

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
