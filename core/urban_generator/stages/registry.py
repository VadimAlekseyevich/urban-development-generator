from __future__ import annotations

import heapq
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, cast

from core.urban_generator.domain import (
    Stage,
    StageContractError,
    validate_stage_metadata,
)


class StageRegistryError(StageContractError):
    """Raised when a set of canonical stages does not form a valid dependency DAG."""


class StageSkipReason(StrEnum):
    """Why a stage is excluded from execution in one immutable plan."""

    REQUESTED = "REQUESTED"
    DEPENDENCY_SKIPPED = "DEPENDENDENCY_SKIPPED"


StageAny = Stage[Any, Any, Any]


@dataclass(frozen=True, slots=True)
class StagePlanEntry:
    """One stage decision in deterministic topological order."""

    stage: StageAny
    skip_reason: StageSkipReason | None = None
    blocked_by: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.stage, Stage):
            raise StageRegistryError("plan entry stage must implement the canonical Stage protocol")
        if self.skip_reason is not None and not isinstance(
            self.skip_reason,
            StageSkipReason,
        ):
            raise StageRegistryError("plan entry skip_reason must be StageSkipReason or None")
        if not isinstance(self.blocked_by, tuple):
            raise StageRegistryError("plan entry blocked_by must be an immutable tuple")
        if self.skip_reason is StageSkipReason.REQUESTED and self.blocked_by:
            raise StageRegistryError("requested skip must not have blocked dependencies")
        if self.skip_reason is StageSkipReason.DEPENDENCY_SKIPPED and not self.blocked_by:
            raise StageRegistryError("dependency skip requires at least one blocked dependency")
        if self.skip_reason is None and self.blocked_by:
            raise StageRegistryError("executable stage must not have blocked dependencies")

    @property
    def should_execute(self) -> bool:
        return self.skip_reason is None


@dataclass(frozen=True, slots=True)
class StageRegistry:
    """Immutable registry that validates canonical Stage metadata as one DAG."""

    stages: tuple[StageAny, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.stages, tuple):
            raise StageRegistryError("stages must be an immutable tuple")
        if not self.stages:
            raise StageRegistryError("stage registry must not be empty")

        validated: list[StageAny] = []
        by_name: dict[str, StageAny] = {}
        for index, candidate in enumerate(self.stages):
            if not isinstance(candidate, Stage):
                raise StageRegistryError(
                    f"stage registry item {index} must implement the canonical Stage protocol"
                )
            stage = cast(StageAny, candidate)
            try:
                validate_stage_metadata(
                    stage.name,
                    stage.version,
                    stage.dependencies,
                )
            except StageContractError as exc:
                raise StageRegistryError(
                    f"invalid stage metadata for {stage.name!r}: {exc}"
                ) from exc

            if stage.name in by_name:
                raise StageRegistryError(f"duplicate stage name: {stage.name}")
            by_name[stage.name] = stage
            validated.append(stage)

        known_names = frozenset(by_name)
        for stage in validated:
            for dependency in stage.dependencies:
                if dependency not in known_names:
                    raise StageRegistryError(
                        f"stage {stage.name} depends on missing stage {dependency}"
                    )

        _validate_acyclic(tuple(validated))
        object.__setattr__(self, "stages", tuple(validated))

    @property
    def names(self) -> tuple[str, ...]:
        """Return registry insertion order without implying execution order."""

        return tuple(stage.name for stage in self.stages)

    @property
    def topological_stages(self) -> tuple[StageAny, ...]:
        """Return dependency-safe order with stable-name tie-breaking."""

        by_name = {stage.name: stage for stage in self.stages}
        indegree = {
            stage.name: len(stage.dependencies)
            for stage in self.stages
        }
        dependents: dict[str, list[str]] = {
            stage.name: []
            for stage in self.stages
        }
        for stage in self.stages:
            for dependency in stage.dependencies:
                dependents[dependency].append(stage.name)

        ready = [
            stage_name
            for stage_name, degree in indegree.items()
            if degree == 0
        ]
        heapq.heapify(ready)

        ordered: list[StageAny] = []
        while ready:
            stage_name = heapq.heappop(ready)
            ordered.append(by_name[stage_name])
            for dependent in sorted(dependents[stage_name]):
                indegree[dependent] -= 1
                if indegree[dependent] == 0:
                    heapq.heappush(ready, dependent)

        if len(ordered) != len(self.stages):
            raise StageRegistryError("stage registry contains an unresolved dependency cycle")
        return tuple(ordered)

    def get(self, stage_name: str) -> StageAny:
        """Return one registered Stage by stable name."""

        for stage in self.stages:
            if stage.name == stage_name:
                return stage
        raise StageRegistryError(f"stage is not registered: {stage_name}")

    def build_plan(
        self,
        *,
        skip_stages: tuple[str, ...] = (),
    ) -> tuple[StagePlanEntry, ...]:
        """Resolve explicit skips and their downstream dependency closure."""

        if not isinstance(skip_stages, tuple):
            raise StageRegistryError("skip_stages must be an immutable tuple")
        if len(skip_stages) != len(set(skip_stages)):
            raise StageRegistryError("skip_stages must not contain duplicates")

        known_names = frozenset(self.names)
        for stage_name in skip_stages:
            if not isinstance(stage_name, str) or not stage_name:
                raise StageRegistryError("skip stage name must be a non-empty string")
            if stage_name not in known_names:
                raise StageRegistryError(f"skip stage is not registered: {stage_name}")

        explicitly_skipped = frozenset(skip_stages)
        skipped_names: set[str] = set()
        plan: list[StagePlanEntry] = []

        for stage in self.topological_stages:
            if stage.name in explicitly_skipped:
                skipped_names.add(stage.name)
                plan.append(
                    StagePlanEntry(
                        stage=stage,
                        skip_reason=StageSkipReason.REQUESTED,
                    )
                )
                continue

            blocked_by = tuple(
                sorted(
                    dependency
                    for dependency in stage.dependencies
                    if dependency in skipped_names
                )
            )
            if blocked_by:
                skipped_names.add(stage.name)
                plan.append(
                    StagePlanEntry(
                        stage=stage,
                        skip_reason=StageSkipReason.DEPENDENCY_SKIPPED,
                        blocked_by=blocked_by,
                    )
                )
                continue

            plan.append(StagePlanEntry(stage=stage))

        return tuple(plan)


def _validate_acyclic(stages: tuple[StageAny, ...]) -> None:
    dependencies = {
        stage.name: stage.dependencies
        for stage in stages
    }
    state: dict[str, int] = {}
    stack: list[str] = []

    def visit(stage_name: str) -> None:
        marker = state.get(stage_name, 0)
        if marker == 2:
            return
        if marker == 1:
            start = stack.index(stage_name)
            cycle = stack[start:] + [stage_name]
            raise StageRegistryError(
                "stage dependency cycle: " + " -> ".join(cycle)
            )

        state[stage_name] = 1
        stack.append(stage_name)
        for dependency in dependencies[stage_name]:
            visit(dependency)
        stack.pop()
        state[stage_name] = 2

    for stage in stages:
        visit(stage.name)
