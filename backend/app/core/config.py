from functools import lru_cache
from pathlib import Path
from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
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
    worker_ingest_max_jobs: int = Field(default=2, ge=1, le=32)
    worker_generation_max_jobs: int = Field(default=1, ge=1, le=32)
    worker_analysis_max_jobs: int = Field(default=1, ge=1, le=32)
    worker_export_max_jobs: int = Field(default=1, ge=1, le=32)
    max_active_jobs_per_project: int = Field(default=16, ge=1, le=128)
    max_active_generation_jobs_per_project: int = Field(default=10, ge=1, le=128)
    max_active_ingest_jobs_per_project: int = Field(default=4, ge=1, le=128)
    max_active_export_jobs_per_project: int = Field(default=4, ge=1, le=128)
    artifact_gc_min_age_hours: int = Field(default=2, ge=1, le=720)
    artifact_gc_max_batch: int = Field(default=50, ge=1, le=500)
    artifact_gc_max_scan: int = Field(default=5000, ge=1, le=100000)
    artifact_gc_dry_run: bool = False
    metrics_bearer_token: SecretStr | None = None
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

    @field_validator("cors_origins")
    @classmethod
    def validate_cors_origins(cls, value: str) -> str:
        """Reject wildcard, opaque, credential-bearing and malformed browser origins."""
        for origin in (part.strip() for part in value.split(",")):
            if not origin:
                continue
            try:
                parsed = urlsplit(origin)
                port = parsed.port
            except ValueError as exc:
                raise ValueError("CORS_ORIGINS contains an invalid origin") from exc
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.path
                or parsed.query
                or parsed.fragment
                or any(character.isspace() for character in parsed.netloc)
                or "*" in parsed.netloc
                or parsed.netloc.endswith(":")
                or port == 0
                or origin != f"{parsed.scheme}://{parsed.netloc}"
            ):
                raise ValueError(
                    "CORS_ORIGINS requires exact http(s) origins without paths or wildcards"
                )
        return value

    @model_validator(mode="after")
    def validate_production_security(self) -> Self:
        if self.app_env.casefold() != "production":
            return self
        if self.app_debug:
            raise ValueError("APP_DEBUG must be false in production")
        for origin in self.cors_origin_list:
            parsed = urlsplit(origin)
            if parsed.scheme != "https" and parsed.hostname not in {
                "localhost", "127.0.0.1", "::1"
            }:
                raise ValueError("production CORS_ORIGINS requires HTTPS for remote origins")
        return self

    @field_validator("metrics_bearer_token")
    @classmethod
    def validate_metrics_token(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and len(value.get_secret_value()) < 32:
            raise ValueError("METRICS_BEARER_TOKEN must be at least 32 characters")
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
