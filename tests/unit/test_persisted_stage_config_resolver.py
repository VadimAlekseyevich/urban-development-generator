"""Fail-closed persisted JSONB -> existing typed canonical StageConfig regression."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from backend.app.adapters.stage_config_resolver import (
    CONFIG_SCHEMA_VERSION,
    MAX_CONFIG_BYTES,
    PREPARED_CONFIG_SCHEMA_VERSION,
    SUPPORTED_CONFIG_STAGE_NAMES,
    CanonicalStageConfigResolver,
    PersistedStageConfigError,
)
from core.urban_generator.domain import ConfigRef
from core.urban_generator.stages import (
    CANONICAL_STAGE_ORDER,
    ConstraintMaskStageConfig,
    PrepareSnapshotStageConfig,
    RoadStageConfig,
    SuitabilityStageConfig,
    ZoningStageConfig,
)
from core.urban_generator.suitability import SuitabilityNormalization
from scripts.validate_generation_config import main

SOURCE = ConfigRef(
    name="generation", ref="generation-run:00000000-0000-0000-0000-000000000001:config"
)


def _stage(config: dict[str, object], *, stage_version: str = "1.0.0") -> dict[str, object]:
    return {"stage_version": stage_version, "config": config}


def _manifest(**stages: dict[str, object]) -> dict[str, object]:
    return {"schema_version": CONFIG_SCHEMA_VERSION, "stages": stages}


def _suitability() -> dict[str, object]:
    return {
        "suitability": {
            "version": "p0-schema-test-v1",
            "factors": [{
                "code": "slope",
                "weight": 1.0,
                "normalization": "IDENTITY",
            }],
            "thresholds": {"minimum_score": 0.3},
        },
        "max_cells": 1024,
    }


def _zoning() -> dict[str, object]:
    return {
        "zoning": {
            "version": "p0-zoning-v1",
            "zones": [{
                "zone_class": name,
                "target_share": 0.25,
                "minimum_area_m2": 100.0,
            } for name in ("residential", "mixed", "public", "recreation")],
        },
        "seed_count": 4,
    }


def test_valid_typed_nested_configs_follow_canonical_order_and_source() -> None:
    # The selection order cannot modify canonical DAG config ordering.
    resolver = CanonicalStageConfigResolver(
        stage_names=("roads", "zoning", "suitability", "evaluate_constraints")
    )
    parsed = resolver.resolve(
        config_json=_manifest(
            roads=_stage({"endpoint_snapping": {"tolerance_m": 1.25}}),
            zoning=_stage(_zoning()),
            suitability=_stage(_suitability()),
            evaluate_constraints=_stage({
                "exclusion_policy": "ANY_TOUCH",
                "max_shapes": 100,
            }),
        ),
        schema_version=CONFIG_SCHEMA_VERSION,
        source=SOURCE,
    )
    assert [item.stage_name for item in parsed] == [
        "evaluate_constraints", "suitability", "zoning", "roads"
    ]
    assert all(item.source is SOURCE for item in parsed)
    assert isinstance(parsed[0].value, ConstraintMaskStageConfig)
    assert parsed[0].value.max_shapes == 100

    suitability = parsed[1].value
    assert isinstance(suitability, SuitabilityStageConfig)
    assert suitability.suitability.factors[0].normalization is SuitabilityNormalization.IDENTITY
    assert suitability.suitability.thresholds.minimum_score == 0.3
    assert suitability.max_cells == 1024

    zoning = parsed[2].value
    assert isinstance(zoning, ZoningStageConfig)
    assert zoning.seed_count == 4
    assert len(zoning.zoning.zones) == 4

    roads = parsed[3].value
    assert isinstance(roads, RoadStageConfig)
    assert roads.endpoint_snapping.tolerance_m == 1.25
    assert roads.endpoint_snapping.max_candidate_pairs == 2_000_000


def test_schema_is_closed_versioned_and_covers_only_concrete_core_stages() -> None:
    schema = CanonicalStageConfigResolver().json_schema()
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["properties"]["schema_version"]["const"] == CONFIG_SCHEMA_VERSION
    stages = schema["properties"]["stages"]
    assert stages["additionalProperties"] is False
    assert tuple(stages["required"]) == SUPPORTED_CONFIG_STAGE_NAMES
    assert set(SUPPORTED_CONFIG_STAGE_NAMES).issubset(CANONICAL_STAGE_ORDER)
    assert len(SUPPORTED_CONFIG_STAGE_NAMES) == 7
    assert "infrastructure" not in stages["properties"]
    assert "prepare_snapshot" not in stages["properties"]
    stage = stages["properties"]["suitability"]
    assert stage["additionalProperties"] is False
    assert stage["properties"]["stage_version"] == {"const": "1.0.0"}
    nested = stage["properties"]["config"]["properties"]["suitability"]
    assert nested["additionalProperties"] is False
    assert "factors" in nested["properties"]
    # Exposing a reproducible JSON schema must be byte-serializable.
    json.dumps(schema, allow_nan=False, sort_keys=True)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda x: x.update({"schema_version": "legacy-v1"}), "embedded"),
        (lambda x: x.update({"dangerous": 1}), "unexpected"),
        (lambda x: x["stages"].update({"roads": _stage({})}), "unexpected"),
        (lambda x: x["stages"].clear(), "missing"),
        (lambda x: x["stages"]["evaluate_constraints"].update(
            {"stage_version": "2.0.0"}
        ), "unsupported Stage version"),
        (lambda x: x["stages"]["evaluate_constraints"].update(
            {"module": "os.system"}
        ), "unexpected"),
        (lambda x: x["stages"]["evaluate_constraints"]["config"].update(
            {"unknown": True}
        ), "unknown options"),
        (lambda x: x["stages"]["evaluate_constraints"]["config"].update(
            {"max_cells": True}
        ), "must be integer"),
        (lambda x: x["stages"]["evaluate_constraints"]["config"].update(
            {"max_shapes": "100"}
        ), "must be integer"),
        (lambda x: x["stages"]["evaluate_constraints"]["config"].update(
            {"exclusion_policy": "MAYBE"}
        ), "unsupported ExclusionRasterizationPolicy"),
    ],
)
def test_invalid_stage_envelope_or_options_are_rejected(
    mutate: object, message: str
) -> None:
    resolver = CanonicalStageConfigResolver(stage_names=("evaluate_constraints",))
    data = _manifest(evaluate_constraints=_stage({}))
    mutate(data)  # type: ignore[operator]
    with pytest.raises(PersistedStageConfigError, match=message):
        resolver.resolve(
            config_json=data,
            schema_version=CONFIG_SCHEMA_VERSION,
            source=SOURCE,
        )


def test_rejects_unsupported_run_schema_before_using_old_payload() -> None:
    resolver = CanonicalStageConfigResolver(stage_names=("evaluate_constraints",))
    with pytest.raises(PersistedStageConfigError, match="unsupported persisted"):
        resolver.resolve(
            config_json=_manifest(evaluate_constraints=_stage({})),
            schema_version="1",
            source=SOURCE,
        )


def test_nested_validation_rejects_invalid_enum_unknown_option_and_nonfinite() -> None:
    resolver = CanonicalStageConfigResolver(stage_names=("suitability",))
    good = _manifest(suitability=_stage(_suitability()))
    for change, expected in (
        ({"normalization": "UNKNOWN"}, "unsupported SuitabilityNormalization"),
        ({"weight": True}, "finite number"),
        ({"weight": "1.0"}, "finite number"),
        ({"weight": float("nan")}, "finite JSON"),
    ):
        bad = copy.deepcopy(good)
        factor = bad["stages"]["suitability"]["config"]["suitability"]["factors"][0]
        factor.update(change)
        with pytest.raises(PersistedStageConfigError, match=expected):
            resolver.resolve(
                config_json=bad, schema_version=CONFIG_SCHEMA_VERSION, source=SOURCE
            )
    bad = copy.deepcopy(good)
    bad["stages"]["suitability"]["config"]["suitability"]["thresholds"]["extra"] = 1
    with pytest.raises(PersistedStageConfigError, match="unknown options"):
        resolver.resolve(
            config_json=bad, schema_version=CONFIG_SCHEMA_VERSION, source=SOURCE
        )


def test_no_partial_acceptance_of_unimplemented_canonical_stages() -> None:
    with pytest.raises(PersistedStageConfigError, match="concrete canonical"):
        CanonicalStageConfigResolver(stage_names=("prepare_snapshot",))
    with pytest.raises(PersistedStageConfigError, match="concrete canonical"):
        CanonicalStageConfigResolver(stage_names=("infrastructure",))
    with pytest.raises(PersistedStageConfigError, match="distinct"):
        CanonicalStageConfigResolver(
            stage_names=("evaluate_constraints", "evaluate_constraints")
        )
    with pytest.raises(PersistedStageConfigError, match="distinct"):
        CanonicalStageConfigResolver(stage_names=())


def test_refuses_oversized_config_with_no_typed_bindings() -> None:
    resolver = CanonicalStageConfigResolver(stage_names=("evaluate_constraints",))
    document = _manifest(evaluate_constraints=_stage({"payload": "x" * MAX_CONFIG_BYTES}))
    with pytest.raises(PersistedStageConfigError, match="exceeds 1 MiB"):
        resolver.resolve(
            config_json=document, schema_version=CONFIG_SCHEMA_VERSION, source=SOURCE
        )


def test_cli_schema_and_invalid_manifest_do_not_access_database_or_worker(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    main(["--schema"])
    schema = json.loads(capsys.readouterr().out)
    assert schema["properties"]["schema_version"]["const"] == CONFIG_SCHEMA_VERSION

    invalid = tmp_path / "config.json"
    invalid.write_text(json.dumps(_manifest(evaluate_constraints=_stage({}))))
    with pytest.raises(SystemExit):
        main(["--manifest", str(invalid)])


def test_v2_prepared_grid_config_is_explicit_and_v1_is_unchanged() -> None:
    resolver = CanonicalStageConfigResolver(
        stage_names=("prepare_snapshot",), schema_version=PREPARED_CONFIG_SCHEMA_VERSION,
    )
    payload = {
        "schema_version": PREPARED_CONFIG_SCHEMA_VERSION,
        "stages": {"prepare_snapshot": _stage({"cell_size_m": 10.0, "max_cells": 100})},
    }
    result = resolver.resolve(
        config_json=payload, schema_version=PREPARED_CONFIG_SCHEMA_VERSION, source=SOURCE,
    )
    assert result[0].value == PrepareSnapshotStageConfig(cell_size_m=10, max_cells=100)
    schema = CanonicalStageConfigResolver(schema_version=PREPARED_CONFIG_SCHEMA_VERSION)
    assert len(schema.json_schema()["properties"]["stages"]["required"]) == 8
    with pytest.raises(PersistedStageConfigError, match="unsupported persisted"):
        resolver.resolve(config_json=payload, schema_version=CONFIG_SCHEMA_VERSION, source=SOURCE)
    with pytest.raises(PersistedStageConfigError, match="unsupported persisted"):
        CanonicalStageConfigResolver().resolve(
            config_json=payload, schema_version=PREPARED_CONFIG_SCHEMA_VERSION, source=SOURCE,
        )
