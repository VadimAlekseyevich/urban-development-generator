from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator
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
    artifact_store_backend: Literal["local", "s3"] = "local"
    storage_root: Path = Path("./storage")
    s3_endpoint_url: str | None = None
    s3_bucket: str | None = None
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_session_token: str | None = None
    s3_region: str = "us-east-1"
    s3_prefix: str = "urban-generator"
    s3_force_path_style: bool = True
    s3_verify_tls: bool = True
    max_upload_size_mb: int = Field(default=1024, ge=1, le=10240)
    cors_origins: str = "http://localhost:5173"

    @field_validator("database_url", "redis_url", "api_v1_prefix")
    @classmethod
    def validate_non_empty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value must not be empty")
        return value

    @model_validator(mode="after")
    def validate_artifact_store(self) -> Self:
        if self.artifact_store_backend != "s3":
            return self
        required = {
            "S3_ENDPOINT_URL": self.s3_endpoint_url,
            "S3_BUCKET": self.s3_bucket,
            "S3_ACCESS_KEY": self.s3_access_key,
            "S3_SECRET_KEY": self.s3_secret_key,
        }
        missing = [
            name
            for name, value in required.items()
            if not value or not value.strip()
        ]
        if missing:
            raise ValueError(
                "S3 artifact storage requires " + ", ".join(sorted(missing))
            )
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
