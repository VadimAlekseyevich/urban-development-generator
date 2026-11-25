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
    StageSkipReason,
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
    assert tuple(stage.name for stage in registry.topological_stages) == (
        CANONICAL_STAGE_ORDER
    )
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



def test_topological_order_is_stable_under_registration_permutations() -> None:
    first = StageRegistry(
        (
            _stage("zeta", "alpha"),
            _stage("beta"),
            _stage("alpha"),
            _stage("omega", "beta"),
        )
    )
    second = StageRegistry(
        (
            _stage("omega", "beta"),
            _stage("alpha"),
            _stage("zeta", "alpha"),
            _stage("beta"),
        )
    )

    expected = ("alpha", "beta", "omega", "zeta")
    assert tuple(stage.name for stage in first.topological_stages) == expected
    assert tuple(stage.name for stage in second.topological_stages) == expected


def test_build_plan_executes_every_stage_when_skip_set_is_empty() -> None:
    registry = StageRegistry(
        (
            _stage("final", "left", "right"),
            _stage("right", "root"),
            _stage("root"),
            _stage("left", "root"),
        )
    )

    plan = registry.build_plan()

    assert tuple(entry.stage.name for entry in plan) == (
        "root",
        "left",
        "right",
        "final",
    )
    assert all(entry.should_execute for entry in plan)
    assert all(entry.skip_reason is None for entry in plan)
    assert all(entry.blocked_by == () for entry in plan)


def test_explicit_skip_propagates_only_to_downstream_dependents() -> None:
    registry = StageRegistry(
        (
            _stage("final", "left", "right"),
            _stage("right", "root"),
            _stage("left", "root"),
            _stage("root"),
        )
    )

    by_name = {
        entry.stage.name: entry
        for entry in registry.build_plan(skip_stages=("left",))
    }

    assert by_name["root"].should_execute is True
    assert by_name["right"].should_execute is True

    assert by_name["left"].skip_reason is StageSkipReason.REQUESTED
    assert by_name["left"].blocked_by == ()

    assert by_name["final"].skip_reason is StageSkipReason.DEPENDENCY_SKIPPED
    assert by_name["final"].blocked_by == ("left",)


def test_explicit_skip_reason_takes_priority_over_dependency_skip() -> None:
    registry = StageRegistry(
        (
            _stage("root"),
            _stage("middle", "root"),
            _stage("final", "middle"),
        )
    )

    by_name = {
        entry.stage.name: entry
        for entry in registry.build_plan(
            skip_stages=("root", "middle"),
        )
    }

    assert by_name["root"].skip_reason is StageSkipReason.REQUESTED
    assert by_name["middle"].skip_reason is StageSkipReason.REQUESTED
    assert by_name["middle"].blocked_by == ()
    assert by_name["final"].skip_reason is StageSkipReason.DEPENDENCY_SKIPPED
    assert by_name["final"].blocked_by == ("middle",)


def test_skip_closure_uses_direct_blocked_dependencies_in_stable_order() -> None:
    registry = StageRegistry(
        (
            _stage("final", "zeta", "alpha"),
            _stage("zeta", "root"),
            _stage("alpha", "root"),
            _stage("root"),
        )
    )

    by_name = {
        entry.stage.name: entry
        for entry in registry.build_plan(skip_stages=("root",))
    }

    assert by_name["alpha"].blocked_by == ("root",)
    assert by_name["zeta"].blocked_by == ("root",)
    assert by_name["final"].blocked_by == ("alpha", "zeta")


def test_skip_plan_rejects_invalid_skip_requests() -> None:
    registry = StageRegistry((_stage("roads"),))

    with pytest.raises(StageRegistryError, match="immutable tuple"):
        registry.build_plan(skip_stages=["roads"])  # type: ignore[arg-type]

    with pytest.raises(StageRegistryError, match="must not contain duplicates"):
        registry.build_plan(skip_stages=("roads", "roads"))

    with pytest.raises(StageRegistryError, match="skip stage is not registered: zoning"):
        registry.build_plan(skip_stages=("zoning",))
