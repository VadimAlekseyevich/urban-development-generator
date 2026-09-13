import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

from core.urban_generator.domain.run_context import RunContext
from core.urban_generator.domain.territory import TerritorySnapshot


class ConstraintContractError(ValueError):
    """Raised when constraint metadata or evaluation results violate the domain contract."""


class ConstraintSeverity(StrEnum):
    """Whether a failed constraint blocks generation or only records a preference violation."""

    HARD = "HARD"
    SOFT = "SOFT"


class ConstraintScope(StrEnum):
    """Canonical domain scopes to which a constraint can apply."""

    TERRITORY = "TERRITORY"
    ZONE = "ZONE"
    ROAD = "ROAD"
    BLOCK = "BLOCK"
    PARCEL = "PARCEL"
    BUILDING = "BUILDING"
    DEMOGRAPHY = "DEMOGRAPHY"
    INFRASTRUCTURE = "INFRASTRUCTURE"


@dataclass(frozen=True, slots=True)
class ConstraintResult:
    """Immutable result of evaluating one constraint against one subject."""

    code: str
    severity: ConstraintSeverity
    scope: ConstraintScope
    passed: bool
    message: str

    def __post_init__(self) -> None:
        validate_constraint_metadata(self.code, self.severity, self.scope)
        if not isinstance(self.passed, bool):
            raise ConstraintContractError("constraint passed flag must be bool")
        if not isinstance(self.message, str) or not self.message.strip():
            raise ConstraintContractError("constraint result message must be a non-empty string")

    @property
    def failed(self) -> bool:
        return not self.passed

    @property
    def blocks_generation(self) -> bool:
        return self.severity is ConstraintSeverity.HARD and self.failed


@dataclass(frozen=True, slots=True)
class ValidationReport:
    """Immutable aggregate of constraint results.

    Only failed HARD constraints make the report invalid. Failed SOFT constraints remain
    visible as violations but do not block generation.
    """

    results: tuple[ConstraintResult, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.results, tuple):
            raise ConstraintContractError("validation results must be an immutable tuple")
        if any(not isinstance(item, ConstraintResult) for item in self.results):
            raise ConstraintContractError(
                "validation results must contain only ConstraintResult values"
            )

    @property
    def failures(self) -> tuple[ConstraintResult, ...]:
        return tuple(result for result in self.results if result.failed)

    @property
    def hard_failures(self) -> tuple[ConstraintResult, ...]:
        return tuple(result for result in self.results if result.blocks_generation)

    @property
    def soft_violations(self) -> tuple[ConstraintResult, ...]:
        return tuple(
            result
            for result in self.results
            if result.severity is ConstraintSeverity.SOFT and result.failed
        )

    @property
    def is_valid(self) -> bool:
        return not self.hard_failures


@runtime_checkable
class Constraint[SubjectT](Protocol):
    """Infrastructure-independent rule contract for one typed subject."""

    code: str
    severity: ConstraintSeverity
    scope: ConstraintScope

    def evaluate(
        self,
        *,
        subject: SubjectT,
        snapshot: TerritorySnapshot,
        context: RunContext,
    ) -> ConstraintResult:
        """Evaluate one subject without HTTP, persistence, queue, or UI dependencies."""


@runtime_checkable
class ConstraintEngine(Protocol):
    """Port used by stages to evaluate the registry for one exact stage/scope pair."""

    def evaluate[SubjectT](
        self,
        *,
        subject: SubjectT,
        stage: str,
        scope: ConstraintScope,
        snapshot: TerritorySnapshot,
        context: RunContext,
    ) -> ValidationReport:
        """Evaluate all rules registered for ``stage`` and ``scope``."""


def validate_constraint_metadata(
    code: str,
    severity: ConstraintSeverity,
    scope: ConstraintScope,
) -> None:
    """Validate stable constraint identity and classification."""

    if not isinstance(code, str) or _CONSTRAINT_CODE_RE.fullmatch(code) is None:
        raise ConstraintContractError(f"invalid constraint code: {code!r}")
    if not isinstance(severity, ConstraintSeverity):
        raise ConstraintContractError("constraint severity must be a ConstraintSeverity value")
    if not isinstance(scope, ConstraintScope):
        raise ConstraintContractError("constraint scope must be a ConstraintScope value")


_CONSTRAINT_CODE_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")
