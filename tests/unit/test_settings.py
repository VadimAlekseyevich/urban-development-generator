from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.app.core.config import Settings


def test_settings_parse_cors_and_storage_path() -> None:
    settings = Settings(cors_origins="http://a.local, http://b.local", storage_root="./tmp-storage")

    assert settings.cors_origin_list == ["http://a.local", "http://b.local"]
    assert settings.storage_root == Path("tmp-storage")


def test_settings_reject_invalid_port() -> None:
    with pytest.raises(ValidationError):
        Settings(app_port=0)


def test_settings_reject_invalid_upload_limit() -> None:
    with pytest.raises(ValidationError):
        Settings(max_upload_size_mb=0)
