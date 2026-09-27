"""Pure reproducible bounded ScenarioBatch matrix expansion."""

from __future__ import annotations

import json
import uuid

import pytest

from backend.app.application.scenario_matrix import (
    ScenarioConfigVariant,
    ScenarioMatrixError,
    ScenarioMatrixSpec,
    expand_scenario_matrix,
)
from core.urban_generator.domain import RunMode

PROJECT = uuid.UUID("00000000-0000-0000-0000-000000000001")


def _variant(name: str, config: dict[str, object]) -> ScenarioConfigVariant:
    return ScenarioConfigVariant.from_config(name=name, config_json=config)


def _spec(
    *,
    seeds: tuple[int, ...] = (7, 3),
    variants: tuple[ScenarioConfigVariant, ...] | None = None,
    dataset_version_ids: tuple[uuid.UUID, ...] = (),
    concurrency_limit: int = 2,
) -> ScenarioMatrixSpec:
    return ScenarioMatrixSpec(
        project_id=PROJECT,
        mode=RunMode.EXPANSION,
        seeds=seeds,
        variants=variants or (
            _variant("high", {"density": 3, "settings": {"b": 2, "a": 1}}),
            _variant("low", {"density": 1}),
        ),
        dataset_version_ids=dataset_version_ids,
        config_schema_version="v1",
        commit_sha="a" * 40,
        concurrency_limit=concurrency_limit,
    )


def test_matrix_is_variant_major_seed_minor_and_permutation_invariant() -> None:
    first = _spec()
    second = _spec(
        seeds=(3, 7),
        variants=(
            _variant("low", {"density": 1}),
            _variant("high", {"settings": {"a": 1, "b": 2}, "density": 3}),
        ),
    )
    assert expand_scenario_matrix(first) == expand_scenario_matrix(second)
    rows = expand_scenario_matrix(first)
    assert [(r.position, r.variant_name, r.seed) for r in rows] == [
        (0, "high", 3),
        (1, "high", 7),
        (2, "low", 3),
        (3, "low", 7),
    ]


def test_config_is_canonical_and_detached_from_mutable_caller_data() -> None:
    supplied: dict[str, object] = {"b": {"x": [2, 1]}, "a": 1}
    variant = _variant("variant", supplied)
    supplied["a"] = 99
    assert variant.canonical_config_json == '{"a":1,"b":{"x":[2,1]}}'
    decoded = variant.config_json
    decoded["a"] = 44
    assert variant.config_json["a"] == 1
    child = expand_scenario_matrix(_spec(
        seeds=(1, 2, 3), variants=(variant,), concurrency_limit=1
    ))[0]
    first = child.config_json
    first["a"] = 33
    assert child.config_json["a"] == 1


@pytest.mark.parametrize("seed_count,variant_count", [(1, 2), (11, 1)])
def test_rejects_product_outside_batch_bounds(seed_count: int, variant_count: int) -> None:
    variants = tuple(_variant(f"v{i}", {"id": i}) for i in range(variant_count))
    with pytest.raises(ScenarioMatrixError, match="3–10"):
        _spec(seeds=tuple(range(seed_count)), variants=variants)


@pytest.mark.parametrize("seeds", [(1, 1, 2), (True, 2, 3), (-1, 2, 3), (2**63, 2, 3)])
def test_rejects_invalid_or_unsafe_persisted_seeds(seeds: tuple[int, ...]) -> None:
    with pytest.raises(ScenarioMatrixError, match="seed"):
        _spec(seeds=seeds, variants=(_variant("one", {"value": 1}),))


def test_accepts_signed_bigint_maximum_without_implicit_seed_wrapping() -> None:
    spec = _spec(
        seeds=(0, 2, 2**63 - 1),
        variants=(_variant("only", {"scenario": "edge"}),),
        concurrency_limit=3,
    )
    assert tuple(row.seed for row in expand_scenario_matrix(spec)) == (0, 2, 2**63 - 1)


def test_rejects_duplicate_labels_full_configs_and_dataset_refs() -> None:
    with pytest.raises(ScenarioMatrixError, match="variant names"):
        _spec(variants=(_variant("dup", {"a": 1}), _variant("dup", {"a": 2})))
    with pytest.raises(ScenarioMatrixError, match="full config"):
        _spec(variants=(_variant("one", {"a": 1}), _variant("two", {"a": 1})))
    ref = uuid.uuid4()
    with pytest.raises(ScenarioMatrixError, match="version IDs"):
        _spec(dataset_version_ids=(ref, ref))


@pytest.mark.parametrize(
    "config",
    [{"x": float("nan")}, {"x": float("inf")}, {"x": object()}],
)
def test_rejects_noncanonical_json_inputs(config: dict[str, object]) -> None:
    with pytest.raises(ScenarioMatrixError, match="finite valid JSON"):
        _variant("bad", config)


def test_rejects_noncanonical_encoded_json_and_bad_names() -> None:
    with pytest.raises(ScenarioMatrixError, match="canonical"):
        ScenarioConfigVariant(name="one", canonical_config_json='{"b":2, "a":1}')
    with pytest.raises(ScenarioMatrixError, match="lowercase"):
        _variant("Not Stable", {"ok": True})
    with pytest.raises(ScenarioMatrixError, match="valid JSON"):
        ScenarioConfigVariant(name="one", canonical_config_json=json.dumps(float("nan")))


def test_rejects_invalid_concurrency_and_provenance() -> None:
    with pytest.raises(ScenarioMatrixError, match="concurrency_limit"):
        _spec(concurrency_limit=True)
    spec = _spec()
    with pytest.raises(ScenarioMatrixError, match="commit_sha"):
        ScenarioMatrixSpec(
            project_id=spec.project_id,
            mode=spec.mode,
            seeds=spec.seeds,
            variants=spec.variants,
            dataset_version_ids=spec.dataset_version_ids,
            config_schema_version=spec.config_schema_version,
            commit_sha="not-real",
            concurrency_limit=spec.concurrency_limit,
        )
