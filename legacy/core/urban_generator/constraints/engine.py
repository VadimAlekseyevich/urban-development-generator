from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from core.urban_generator.domain.constraints import (
    Constraint,
    ConstraintContractError,
    ConstraintEngine,
    ConstraintResult,
    ConstraintScope,
    ValidationReport,
    validate_constraint_metadata,
)
from core.urban_generator.domain.run_context import RunContext
from core.urban_generator.domain.territory import TerritorySnapshot


class ConstraintRegistryError(ConstraintContractError):
    """Raised when registry metadata or registrations are invalid."""


@dataclass(frozen=True, slots=True)
class ConstraintRegistration:
    """One immutable binding of a rule to an algorithm stage."""

    stage: str
    constraint: Constraint[Any]

    def __post_init__(self) -> None:
        _validate_stage(self.stage)
        if not isinstance(self.constraint, Constraint):
            raise ConstraintRegistryError("registered value must implement Constraint")
        validate_constraint_metadata(
            self.constraint.code,
            self.constraint.severity,
            self.constraint.scope,
        )


class ConstraintRegistry:
    """Deterministic in-memory registry keyed by exact ``(stage, scope, code)``."""

    def __init__(self, registrations: tuple[ConstraintRegistration, ...] = ()) -> None:
        if not isinstance(registrations, tuple):
            raise ConstraintRegistryError("constraint registrations must be an immutable tuple")
        self._rules: dict[
            tuple[str, ConstraintScope],
            dict[str, Constraint[Any]],
        ] = {}
        for registration in registrations:
            if not isinstance(registration, ConstraintRegistration):
                raise ConstraintRegistryError(
                    "constraint registrations must contain ConstraintRegistration values"
                )
            self.register(stage=registration.stage, constraint=registration.constraint)

    def register(self, *, stage: str, constraint: Constraint[Any]) -> None:
        """Register one rule for a stage; duplicate stage/scope/code bindings are rejected."""

        registration = ConstraintRegistration(stage=stage, constraint=constraint)
        key = (registration.stage, registration.constraint.scope)
        bucket = self._rules.setdefault(key, {})
        code = registration.constraint.code
        if code in bucket:
            raise ConstraintRegistryError(
                "duplicate constraint registration: "
                f"stage={stage!r}, scope={registration.constraint.scope.value}, code={code!r}"
            )
        bucket[code] = registration.constraint

    def rules_for(
        self,
        *,
        stage: str,
        scope: ConstraintScope,
    ) -> tuple[Constraint[Any], ...]:
        """Return rules in stable code order for one exact stage/scope pair."""

        _validate_stage(stage)
        if not isinstance(scope, ConstraintScope):
            raise ConstraintRegistryError("constraint scope must be a ConstraintScope value")
        bucket = self._rules.get((stage, scope), {})
        return tuple(bucket[code] for code in sorted(bucket))

    @property
    def registrations(self) -> tuple[ConstraintRegistration, ...]:
        """Return an immutable deterministic registry snapshot for diagnostics/tests."""

        registrations = [
            ConstraintRegistration(stage=stage, constraint=constraint)
            for (stage, _scope), bucket in self._rules.items()
            for constraint in bucket.values()
        ]
        return tuple(
            sorted(
                registrations,
                key=lambda item: (
                    item.stage,
                    item.constraint.scope.value,
                    item.constraint.code,
                ),
            )
        )


class RegisteredConstraintEngine(ConstraintEngine):
    """Evaluate registered rules while enforcing their result metadata contract."""

    def __init__(self, registry: ConstraintRegistry) -> None:
        if not isinstance(registry, ConstraintRegistry):
            raise ConstraintRegistryError("engine registry must be a ConstraintRegistry")
        self._registry = registry

    def evaluate[SubjectT](
        self,
        *,
        subject: SubjectT,
        stage: str,
        scope: ConstraintScope,
        snapshot: TerritorySnapshot,
        context: RunContext,
    ) -> ValidationReport:
        results: list[ConstraintResult] = []
        for constraint in self._registry.rules_for(stage=stage, scope=scope):
            result = constraint.evaluate(
                subject=subject,
                snapshot=snapshot,
                context=context,
            )
            _validate_result(
                result=result,
                constraint=constraint,
                stage=stage,
            )
            results.append(result)
        return ValidationReport(results=tuple(results))


def _validate_result(
    *,
    result: object,
    constraint: Constraint[Any],
    stage: str,
) -> None:
    if not isinstance(result, ConstraintResult):
        raise ConstraintContractError(
            f"constraint {constraint.code!r} at stage {stage!r} must return ConstraintResult"
        )
    if result.code != constraint.code:
        raise ConstraintContractError(
            f"constraint {constraint.code!r} returned mismatched code {result.code!r}"
        )
    if result.severity is not constraint.severity:
        raise ConstraintContractError(
            f"constraint {constraint.code!r} returned mismatched severity "
            f"{result.severity.value}"
        )
    if result.scope is not constraint.scope:
        raise ConstraintContractError(
            f"constraint {constraint.code!r} returned mismatched scope {result.scope.value}"
        )


def _validate_stage(stage: str) -> None:
    if not isinstance(stage, str) or _STAGE_NAME_RE.fullmatch(stage) is None:
        raise ConstraintRegistryError(f"invalid constraint stage: {stage!r}")


_STAGE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
