"""Fast guards for S14 backup manifest; full pg_dump/pg_restore runs in CI."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from scripts.metadata_restore_smoke import (
    SCHEMA,
    _ids,
    _reference_values,
    verify,
)


def test_restore_manifest_keeps_distinct_external_artifact_identities() -> None:
    ids = _ids()
    refs = _reference_values(ids)

    assert len(ids) == 10
    assert len(set(ids.values())) == len(ids)
    assert all(str(uuid.UUID(value)) == value for value in ids.values())
    assert refs["upload_uri"].startswith(f"artifact://uploads/{ids['project']}/")
    assert refs["output_uri"].startswith(f"artifact://runs/{ids['run']}/")
    assert refs["upload_uri"] != refs["output_uri"]
    assert refs["upload_sha256"] != refs["output_sha256"]
    assert refs["stage_fingerprint"].startswith("sha256:")


@pytest.mark.parametrize("schema", ["s14-metadata-restore-v0", "", None])
def test_restore_verification_rejects_wrong_manifest_schema(
    tmp_path: Path, schema: str | None,
) -> None:
    manifest = tmp_path / "restore.json"
    manifest.write_text(json.dumps({"schema": schema}), encoding="utf-8")
    with pytest.raises(ValueError, match="manifest schema"):
        verify(database_url="not-a-postgres-url", manifest_path=manifest)


def test_restore_schema_identifier_is_fixed() -> None:
    assert SCHEMA == "s14-metadata-restore-v1"
