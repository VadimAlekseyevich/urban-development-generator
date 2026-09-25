from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from core.urban_generator.domain import (
    Stage,
    StageContractError,
    validate_stage_metadata,
)


class StageRegistryError(StageContractError):
    """Raised when a set of canonical stages does not form a valid dependency DAG."""


StageAny = Stage[Any, Any, Any]


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

    def get(self, stage_name: str) -> StageAny:
        """Return one registered Stage by stable name."""

        for stage in self.stages:
            if stage.name == stage_name:
                return stage
        raise StageRegistryError(f"stage is not registered: {stage_name}")


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
