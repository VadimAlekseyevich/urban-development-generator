"""Strict manifest, semantic fingerprint and zero-DB preview regression."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.app.application.experiment_matrix import (
    ExperimentManifest,
    ExperimentMatrixError,
    build_experiment_plan,
)
from scripts.experiment_matrix import main

PROJECT_A = "00000000-0000-0000-0000-000000000001"
PROJECT_B = "00000000-0000-0000-0000-000000000002"
VERSION_A = "00000000-0000-0000-0000-000000000011"
VERSION_B = "00000000-0000-0000-0000-000000000012"


def _payload() -> dict[str, object]:
    return {
        "schema_version": "s15-experiment-matrix-v1",
        "experiment_id": "density-ablation",
        "territories": [
            {
                "name": "territory-b",
                "project_id": PROJECT_B,
                "mode": "FROM_SCRATCH",
                "dataset_version_ids": [VERSION_B],
            },
            {
                "name": "territory-a",
                "project_id": PROJECT_A,
                "mode": "EXPANSION",
                "dataset_version_ids": [VERSION_A],
            },
        ],
        "cells": [
            {"config_name": "dense", "ablation_name": "no_soft",
             "config_json": {"density": 3, "soft": False}},
            {"config_name": "light", "ablation_name": "full",
             "config_json": {"density": 1, "soft": True}},
            {"config_name": "dense", "ablation_name": "full",
             "config_json": {"soft": True, "density": 3}},
            {"config_name": "light", "ablation_name": "no_soft",
             "config_json": {"soft": False, "density": 1}},
        ],
        "seeds": [11, 7],
        "config_schema_version": "experiment-fixture-v1",
        "commit_sha": "a" * 40,
        "concurrency_limit": 2,
    }


def _load(payload: dict[str, object] | None = None) -> ExperimentManifest:
    return ExperimentManifest.model_validate_json(json.dumps(payload or _payload()))


def test_plan_is_full_factorial_canonical_and_has_no_fake_run_ids() -> None:
    first = _load()
    plan = build_experiment_plan(first)
    assert plan.plan_sha256.startswith("sha256:")
    assert len(plan.plan_sha256) == 71
    assert [t.name for t in plan.territories] == ["territory-a", "territory-b"]
    assert len(plan.territories) == 2
    for territory in plan.territories:
        assert len(territory.runs) == 8
        assert [row.position for row in territory.runs] == list(range(8))
        assert [(row.config_name, row.ablation_name, row.seed)
                for row in territory.runs] == [
            ("dense", "full", 7), ("dense", "full", 11),
            ("dense", "no_soft", 7), ("dense", "no_soft", 11),
            ("light", "full", 7), ("light", "full", 11),
            ("light", "no_soft", 7), ("light", "no_soft", 11),
        ]
    assert "run_id" not in json.dumps(plan.as_json())
    assert "batch_id" not in json.dumps(plan.as_json())

    reordered = _payload()
    reordered["territories"] = list(reversed(reordered["territories"]))  # type: ignore[arg-type]
    reordered["cells"] = list(reversed(reordered["cells"]))  # type: ignore[arg-type]
    reordered["seeds"] = [7, 11]
    second = build_experiment_plan(_load(reordered))
    assert second == plan

    changed = _payload()
    changed["commit_sha"] = "b" * 40
    assert build_experiment_plan(_load(changed)).plan_sha256 != plan.plan_sha256


def test_scenario_specs_are_normal_full_config_matrix_inputs() -> None:
    manifest = _load()
    spec = manifest.to_scenario_spec(manifest.territory("territory-a"))
    assert spec.project_id == uuid.UUID(PROJECT_A)
    assert spec.mode.value == "EXPANSION"
    assert len(spec.seeds) * len(spec.variants) == 8
    assert {variant.name for variant in spec.variants} == {
        "dense__full", "dense__no_soft", "light__full", "light__no_soft"
    }
    assert all(isinstance(variant.config_json, dict) for variant in spec.variants)
    with pytest.raises(ExperimentMatrixError, match="unknown"):
        manifest.territory("missing")
    with pytest.raises(ExperimentMatrixError, match="does not belong"):
        manifest.to_scenario_spec(_load({**_payload(), "experiment_id": "other"}).territories[0])


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ({"schema_version": "unknown"}, "schema_version"),
        ({"unexpected": 1}, "unexpected"),
        ({"seeds": [7, 7]}, "distinct"),
        ({"seeds": [True, 11]}, "int"),
        ({"seeds": [-1, 11]}, "seed"),
        ({"seeds": [2**63, 11]}, "BIGINT"),
        ({"commit_sha": "untracked"}, "commit_sha"),
        ({"cells": [
            {"config_name": "dense", "ablation_name": "full",
             "config_json": {"density": 3}}
        ]}, "3"),
    ],
)
def test_invalid_manifest_is_rejected(
    mutation: dict[str, object], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        _load({**_payload(), **mutation})


def test_incomplete_factorial_duplicate_names_and_configs_rejected() -> None:
    payload = _payload()
    payload["cells"] = payload["cells"][:-1]  # type: ignore[index]
    with pytest.raises(ValidationError, match="full config"):
        _load(payload)
    payload = _payload()
    payload["cells"] = [payload["cells"][0], *payload["cells"]]  # type: ignore[index]
    with pytest.raises(ValidationError, match="duplicate"):
        _load(payload)
    payload = _payload()
    payload["cells"][0]["config_json"] = {"density": 3, "soft": True}  # type: ignore[index]
    with pytest.raises(ValidationError, match="full config snapshots"):
        _load(payload)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), object()])
def test_reject_nonfinite_or_non_json_configs(value: object) -> None:
    payload = _payload()
    payload["cells"][0]["config_json"] = {"bad": value}  # type: ignore[index]
    with pytest.raises((ValueError, TypeError)):
        _load(payload)


def test_preview_cli_never_submits_and_schema_is_available(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(_payload()), encoding="utf-8")
    main(["--manifest", str(path)])
    printed = json.loads(capsys.readouterr().out)
    assert printed["plan_sha256"] == build_experiment_plan(_load()).plan_sha256
    assert len(printed["territories"]) == 2
    assert "run_id" not in json.dumps(printed)

    main(["--schema"])
    schema = json.loads(capsys.readouterr().out)
    assert schema["properties"]["schema_version"]
    assert schema["properties"]["territories"]
    with pytest.raises(SystemExit):
        main(["--manifest", str(path), "--execute", "--territory", "territory-a"])
    assert not list(tmp_path.glob("*.receipt.json"))
