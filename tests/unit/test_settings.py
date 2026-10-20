from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.app.core.config import Settings


def test_settings_parse_cors_and_storage_path() -> None:
    settings = Settings(cors_origins="http://a.local, http://b.local", storage_root="./tmp-storage")

    assert settings.cors_origin_list == ["http://a.local", "http://b.local"]
    assert settings.storage_root == Path("tmp-storage")


@pytest.mark.parametrize(
    "origins",
    [
        "*",
        "null",
        "file:///tmp/test",
        "http://example.test/",
        "https://example.test/path",
        "https://example.test?token=123",
        "https://example.test#fragment",
        "https://user:pass@example.test",
        "https://example.test:99999",
        "https://example.test:",
        "https://*.example.test",
        "https://example.test, *",
        "http://[broken",
    ],
)
def test_settings_reject_unsafe_cors_origins(origins: str) -> None:
    with pytest.raises(ValidationError, match="CORS_ORIGINS"):
        Settings(cors_origins=origins)


def test_settings_supports_same_origin_only_policy() -> None:
    settings = Settings(cors_origins="")
    assert settings.cors_origin_list == []


def test_settings_accepts_strict_https_and_loopback_origins() -> None:
    settings = Settings(
        app_env="production",
        app_debug=False,
        cors_origins="https://maps.example.test,http://127.0.0.1:8080",
    )
    assert settings.cors_origin_list == [
        "https://maps.example.test",
        "http://127.0.0.1:8080",
    ]


def test_settings_rejects_insecure_production_cors_origin() -> None:
    with pytest.raises(ValidationError, match="requires HTTPS"):
        Settings(
            app_env="production",
            app_debug=False,
            cors_origins="http://maps.example.test",
        )


def test_settings_rejects_debug_enabled_in_production() -> None:
    with pytest.raises(ValidationError, match="APP_DEBUG"):
        Settings(app_env="production", app_debug=True, cors_origins="")


def test_settings_reject_invalid_port() -> None:
    with pytest.raises(ValidationError):
        Settings(app_port=0)


def test_settings_reject_invalid_upload_limit() -> None:
    with pytest.raises(ValidationError):
        Settings(max_upload_size_mb=0)
