import uuid
from dataclasses import dataclass
from enum import StrEnum


class RunSemanticsError(ValueError):
    """Raised when a run violates fixed/generated state semantics."""


class RunMode(StrEnum):
    """Supported generation modes exposed to the infrastructure-independent core."""

    EXPANSION = "EXPANSION"
    FROM_SCRATCH = "FROM_SCRATCH"


class DataOrigin(StrEnum):
    """Origin of a world-state layer."""

    SOURCE = "SOURCE"
    GENERATED = "GENERATED"


class StateOwnership(StrEnum):
    """Mutation/ownership semantics for a world-state layer."""

    FIXED = "FIXED"
    GENERATED = "GENERATED"


@dataclass(frozen=True, slots=True)
class WorldStateContract:
    """Typed ownership contract for fixed source state and generated run state."""

    origin: DataOrigin
    ownership: StateOwnership
    run_id: uuid.UUID | None = None

    def __post_init__(self) -> None:
        if self.ownership is StateOwnership.FIXED:
            if self.origin is not DataOrigin.SOURCE:
                raise RunSemanticsError("fixed world state must originate from source data")
            if self.run_id is not None:
                raise RunSemanticsError("fixed source state cannot be owned by a generation run")
            return

        if self.origin is not DataOrigin.GENERATED:
            raise RunSemanticsError("generated world state must have GENERATED origin")
        if not isinstance(self.run_id, uuid.UUID):
            raise RunSemanticsError("generated world state must be owned by a run UUID")

    @classmethod
    def fixed_source(cls) -> "WorldStateContract":
        return cls(origin=DataOrigin.SOURCE, ownership=StateOwnership.FIXED)

    @classmethod
    def generated_for(cls, run_id: uuid.UUID) -> "WorldStateContract":
        return cls(
            origin=DataOrigin.GENERATED,
            ownership=StateOwnership.GENERATED,
            run_id=run_id,
        )

    @property
    def is_fixed_source(self) -> bool:
        return self.origin is DataOrigin.SOURCE and self.ownership is StateOwnership.FIXED

    @property
    def is_generated(self) -> bool:
        return (
            self.origin is DataOrigin.GENERATED
            and self.ownership is StateOwnership.GENERATED
        )


@dataclass(frozen=True, slots=True)
class RunSemantics:
    """Mode-specific rules shared by all future generation stages."""

    mode: RunMode

    def __post_init__(self) -> None:
        if not isinstance(self.mode, RunMode):
            raise RunSemanticsError("mode must be a RunMode value")

    @property
    def requires_existing_fixed_urban_state(self) -> bool:
        return self.mode is RunMode.EXPANSION

    @property
    def allows_empty_fixed_urban_state(self) -> bool:
        return self.mode is RunMode.FROM_SCRATCH

    def validate_fixed_urban_state(self, *, has_fixed_urban_state: bool) -> None:
        if not isinstance(has_fixed_urban_state, bool):
            raise RunSemanticsError("has_fixed_urban_state must be a boolean")
        if self.requires_existing_fixed_urban_state and not has_fixed_urban_state:
            raise RunSemanticsError(
                "EXPANSION requires an existing fixed urban state; "
                "FROM_SCRATCH is the mode for an empty fixed urban state"
            )
