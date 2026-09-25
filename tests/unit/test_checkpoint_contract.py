from __future__ import annotations

from dataclasses import fields

import pytest

from backend.app.application.checkpoints import (
    CheckpointContractError,
    CheckpointIdentity,
    DependencyOutputFingerprint,
    build_config_hash,
    build_resolved_input_hash,
)
from core.urban_generator.domain import build_stage_fingerprint


def _dependency(
    stage_name: str,
    payload: str,
) -> DependencyOutputFingerprint:
    return DependencyOutputFingerprint(
        stage_name=stage_name,
        output_fingerprint=build_stage_fingerprint(payload),
    )


def _identity(
    *,
    stage_name: str = "infrastructure",
    stage_version: str = "1.0.0",
    input_hash: str | None = None,
    config_hash: str | None = None,
) -> CheckpointIdentity:
    return CheckpointIdentity(
        stage_name=stage_name,
        stage_version=stage_version,
        input_hash=input_hash
        or build_resolved_input_hash(
            input_parts=("territory:v1",),
            expected_dependencies=("demography", "roads"),
            dependency_outputs=(
                _dependency("demography", "demography:v1"),
                _dependency("roads", "roads:v1"),
            ),
        ),
        config_hash=config_hash or build_config_hash("infrastructure-config:v1"),
    )


def test_checkpoint_identity_requires_exact_stage_and_hash_match() -> None:
    expected = _identity()

    assert expected.is_eligible_match(_identity()) is True
    assert expected.is_eligible_match(
        _identity(stage_name="final_validation")
    ) is False
    assert expected.is_eligible_match(
        _identity(stage_version="1.0.1")
    ) is False
    assert expected.is_eligible_match(
        _identity(
            input_hash=build_resolved_input_hash(
                input_parts=("territory:v2",),
                expected_dependencies=("demography", "roads"),
                dependency_outputs=(
                    _dependency("demography", "demography:v1"),
                    _dependency("roads", "roads:v1"),
                ),
            )
        )
    ) is False
    assert expected.is_eligible_match(
        _identity(config_hash=build_config_hash("infrastructure-config:v2"))
    ) is False


def test_resolved_input_hash_includes_dependency_output_fingerprints() -> None:
    first = build_resolved_input_hash(
        input_parts=("snapshot:v1", b"stage-input"),
        expected_dependencies=("roads",),
        dependency_outputs=(_dependency("roads", "roads-output:v1"),),
    )
    second = build_resolved_input_hash(
        input_parts=("snapshot:v1", b"stage-input"),
        expected_dependencies=("roads",),
        dependency_outputs=(_dependency("roads", "roads-output:v2"),),
    )

    assert first != second
    assert first.startswith("sha256:")
    assert len(first) == 71


def test_dependency_output_order_must_match_stage_dependency_order() -> None:
    with pytest.raises(
        CheckpointContractError,
        match="exactly match Stage.dependencies",
    ):
        build_resolved_input_hash(
            input_parts=("snapshot:v1",),
            expected_dependencies=("demography", "roads"),
            dependency_outputs=(
                _dependency("roads", "roads-output:v1"),
                _dependency("demography", "demography-output:v1"),
            ),
        )


def test_dependency_order_is_hash_significant_even_for_same_fingerprints() -> None:
    roads = _dependency("roads", "shared")
    demography = _dependency("demography", "shared")

    first = build_resolved_input_hash(
        input_parts=("snapshot:v1",),
        expected_dependencies=("roads", "demography"),
        dependency_outputs=(roads, demography),
    )
    second = build_resolved_input_hash(
        input_parts=("snapshot:v1",),
        expected_dependencies=("demography", "roads"),
        dependency_outputs=(demography, roads),
    )

    assert first != second


def test_input_hash_and_config_hash_use_independent_namespaces() -> None:
    input_hash = build_resolved_input_hash(
        input_parts=("same-payload",),
        expected_dependencies=(),
        dependency_outputs=(),
    )
    config_hash = build_config_hash("same-payload")

    assert input_hash != config_hash


def test_config_hash_is_deterministic_order_and_type_sensitive() -> None:
    first = build_config_hash("v1", b"payload")
    same = build_config_hash("v1", b"payload")
    reordered = build_config_hash(b"payload", "v1")
    different_type = build_config_hash("v1", "payload")

    assert first == same
    assert first != reordered
    assert first != different_type


def test_current_stage_output_fingerprint_is_not_part_of_checkpoint_identity() -> None:
    identity_fields = tuple(field.name for field in fields(CheckpointIdentity))

    assert identity_fields == (
        "stage_name",
        "stage_version",
        "input_hash",
        "config_hash",
    )
    assert "output_fingerprint" not in identity_fields


def test_resolved_input_hash_requires_complete_dependency_provenance() -> None:
    with pytest.raises(
        CheckpointContractError,
        match="exactly match Stage.dependencies",
    ):
        build_resolved_input_hash(
            input_parts=("snapshot:v1",),
            expected_dependencies=("roads",),
            dependency_outputs=(),
        )

    with pytest.raises(
        CheckpointContractError,
        match="exactly match Stage.dependencies",
    ):
        build_resolved_input_hash(
            input_parts=("snapshot:v1",),
            expected_dependencies=(),
            dependency_outputs=(_dependency("roads", "roads-output:v1"),),
        )


def test_resolved_input_hash_rejects_empty_total_provenance() -> None:
    with pytest.raises(
        CheckpointContractError,
        match="requires input parts or dependency outputs",
    ):
        build_resolved_input_hash(
            input_parts=(),
            expected_dependencies=(),
            dependency_outputs=(),
        )


def test_checkpoint_contract_rejects_invalid_hash_and_dependency_metadata() -> None:
    with pytest.raises(CheckpointContractError, match="input_hash"):
        CheckpointIdentity(
            stage_name="roads",
            stage_version="1.0.0",
            input_hash="invalid",
            config_hash=build_config_hash("v1"),
        )

    with pytest.raises(
        CheckpointContractError,
        match="duplicate expected dependency",
    ):
        build_resolved_input_hash(
            input_parts=("snapshot:v1",),
            expected_dependencies=("roads", "roads"),
            dependency_outputs=(
                _dependency("roads", "first"),
                _dependency("roads", "second"),
            ),
        )


def test_hash_builders_reject_mutable_or_noncanonical_parts() -> None:
    with pytest.raises(CheckpointContractError, match="immutable tuple"):
        build_resolved_input_hash(
            input_parts=["snapshot:v1"],  # type: ignore[arg-type]
            expected_dependencies=(),
            dependency_outputs=(),
        )

    with pytest.raises(CheckpointContractError, match="str or bytes"):
        build_config_hash("v1", 42)  # type: ignore[arg-type]
