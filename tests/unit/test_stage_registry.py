from __future__ import annotations

from dataclasses import dataclass

import pytest

from core.urban_generator.domain import (
    RunContext,
    StageResult,
    TerritorySnapshot,
)
from core.urban_generator.stages import (
    CANONICAL_STAGE_DEPENDENCIES,
    CANONICAL_STAGE_ORDER,
    ROADS_STAGE,
    StageRegistry,
    StageRegistryError,
)


@dataclass(frozen=True, slots=True)
class MetadataStage:
    name: str
    dependencies: tuple[str, ...]
    version: str = "1.0.0"

    def validate_input(self, value: object) -> object:
        return value

    def execute(
        self,
        *,
        snapshot: TerritorySnapshot,
        context: RunContext,
        stage_input: object,
        config: object,
    ) -> StageResult[object]:
        raise AssertionError("metadata-only test stage must not execute")


def _stage(
    name: str,
    *dependencies: str,
    version: str = "1.0.0",
) -> MetadataStage:
    return MetadataStage(
        name=name,
        version=version,
        dependencies=tuple(dependencies),
    )


def test_registry_accepts_full_canonical_dag_independent_of_input_order() -> None:
    stages = tuple(
        MetadataStage(
            name=stage_name,
            dependencies=CANONICAL_STAGE_DEPENDENCIES[stage_name],
        )
        for stage_name in reversed(CANONICAL_STAGE_ORDER)
    )

    registry = StageRegistry(stages)

    assert registry.names == tuple(reversed(CANONICAL_STAGE_ORDER))
    assert set(registry.names) == set(CANONICAL_STAGE_ORDER)
    assert registry.get(ROADS_STAGE).dependencies == CANONICAL_STAGE_DEPENDENCIES[
        ROADS_STAGE
    ]


def test_registry_rejects_duplicate_stage_names() -> None:
    with pytest.raises(StageRegistryError, match="duplicate stage name: roads"):
        StageRegistry(
            (
                _stage("roads"),
                _stage("roads", version="2.0.0"),
            )
        )


def test_registry_rejects_missing_dependency() -> None:
    with pytest.raises(
        StageRegistryError,
        match="stage roads depends on missing stage zoning",
    ):
        StageRegistry((_stage("roads", "zoning"),))


def test_registry_rejects_dependency_cycle() -> None:
    with pytest.raises(
        StageRegistryError,
        match=r"stage dependency cycle: roads -> blocks -> buildings -> roads",
    ):
        StageRegistry(
            (
                _stage("roads", "blocks"),
                _stage("blocks", "buildings"),
                _stage("buildings", "roads"),
            )
        )


def test_registry_wraps_invalid_stage_metadata() -> None:
    with pytest.raises(
        StageRegistryError,
        match="invalid stage metadata.*cannot depend on itself",
    ):
        StageRegistry((_stage("roads", "roads"),))


def test_registry_requires_canonical_stage_implementations() -> None:
    with pytest.raises(
        StageRegistryError,
        match="must implement the canonical Stage protocol",
    ):
        StageRegistry((object(),))  # type: ignore[arg-type]


def test_registry_requires_non_empty_immutable_tuple() -> None:
    with pytest.raises(StageRegistryError, match="must not be empty"):
        StageRegistry(())

    with pytest.raises(StageRegistryError, match="immutable tuple"):
        StageRegistry([])  # type: ignore[arg-type]


def test_registry_lookup_rejects_unknown_stage() -> None:
    registry = StageRegistry((_stage("roads"),))

    with pytest.raises(
        StageRegistryError,
        match="stage is not registered: zoning",
    ):
        registry.get("zoning")
