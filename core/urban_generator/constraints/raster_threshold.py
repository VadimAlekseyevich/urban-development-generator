from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from core.urban_generator.domain.constraints import (
    ConstraintContractError,
    ConstraintResult,
    ConstraintScope,
    ConstraintSeverity,
    validate_constraint_metadata,
)
from core.urban_generator.domain.crs import require_working_crs
from core.urban_generator.domain.run_context import RunContext
from core.urban_generator.domain.territory import TerritorySnapshot


class RasterThresholdError(ConstraintContractError):
    """Raised when raster threshold inputs violate the sampling contract."""


class RasterThresholdSampleLimitError(RasterThresholdError):
    """Raised instead of reading an unbounded number of raster cells."""


class RasterThresholdComparison(StrEnum):
    AT_MOST = "AT_MOST"
    AT_LEAST = "AT_LEAST"


class RasterNoDataPolicy(StrEnum):
    REJECT = "REJECT"
    IGNORE = "IGNORE"


class RasterThresholdHitReason(StrEnum):
    THRESHOLD = "THRESHOLD"
    NODATA = "NODATA"
    NO_VALID_SAMPLES = "NO_VALID_SAMPLES"


@dataclass(frozen=True, slots=True)
class RasterWindow:
    """One bounded raster window in pixel coordinates."""

    row_off: int
    col_off: int
    height: int
    width: int

    def __post_init__(self) -> None:
        for field_name in ("row_off", "col_off", "height", "width"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise RasterThresholdError(f"{field_name} must be an integer")
        if self.row_off < 0 or self.col_off < 0:
            raise RasterThresholdError("raster window offsets must be non-negative")
        if self.height <= 0 or self.width <= 0:
            raise RasterThresholdError("raster window dimensions must be positive")

    @property
    def cell_count(self) -> int:
        return self.height * self.width


class RasterThresholdSource(Protocol):
    """Minimal windowed raster port required by the core threshold rule."""

    working_srid: int
    width: int
    height: int

    def read_window(
        self,
        *,
        window: RasterWindow,
    ) -> tuple[tuple[float | int | None, ...], ...]:
        """Return exactly ``height x width`` cells for the requested window."""


@dataclass(frozen=True, slots=True)
class RasterThresholdPolicy:
    """Threshold, nodata, and bounded sampling policy."""

    threshold: float
    comparison: RasterThresholdComparison = RasterThresholdComparison.AT_MOST
    nodata_policy: RasterNoDataPolicy = RasterNoDataPolicy.REJECT
    max_windows: int = 128
    max_sample_cells: int = 100_000

    def __post_init__(self) -> None:
        if isinstance(self.threshold, bool) or not isinstance(self.threshold, (int, float)):
            raise RasterThresholdError("threshold must be a finite number")
        threshold = float(self.threshold)
        if not math.isfinite(threshold):
            raise RasterThresholdError("threshold must be a finite number")
        object.__setattr__(self, "threshold", threshold)
        if not isinstance(self.comparison, RasterThresholdComparison):
            raise RasterThresholdError(
                "comparison must be a RasterThresholdComparison value"
            )
        if not isinstance(self.nodata_policy, RasterNoDataPolicy):
            raise RasterThresholdError("nodata_policy must be a RasterNoDataPolicy value")
        _require_positive_int("max_windows", self.max_windows)
        _require_positive_int("max_sample_cells", self.max_sample_cells)


@dataclass(frozen=True, slots=True)
class RasterThresholdSubject:
    """Raster windows relevant to one candidate, expressed in the project working CRS."""

    windows: tuple[RasterWindow, ...]
    working_srid: int

    def __post_init__(self) -> None:
        require_working_crs(self.working_srid)
        if not isinstance(self.windows, tuple):
            raise RasterThresholdError("raster windows must be an immutable tuple")
        if not self.windows:
            raise RasterThresholdError("raster threshold subject must contain at least one window")
        if any(not isinstance(window, RasterWindow) for window in self.windows):
            raise RasterThresholdError("raster windows must contain only RasterWindow values")


@dataclass(frozen=True, slots=True)
class RasterThresholdHit:
    """Deterministic explanation of the first raster threshold failure."""

    reason: RasterThresholdHitReason
    window_index: int | None = None
    row: int | None = None
    col: int | None = None
    value: float | None = None


class RasterThresholdEvaluator:
    """Reusable bounded window reader for repeated threshold checks."""

    def __init__(
        self,
        *,
        source: RasterThresholdSource,
        policy: RasterThresholdPolicy,
    ) -> None:
        if not isinstance(policy, RasterThresholdPolicy):
            raise RasterThresholdError("policy must be a RasterThresholdPolicy")
        _validate_source_metadata(source)
        self.source = source
        self.policy = policy
        self.working_srid = source.working_srid

    def check(self, subject: RasterThresholdSubject) -> RasterThresholdHit | None:
        if not isinstance(subject, RasterThresholdSubject):
            raise RasterThresholdError("subject must be a RasterThresholdSubject")
        if subject.working_srid != self.working_srid:
            raise RasterThresholdError(
                "subject working_srid must match raster threshold source working_srid"
            )
        if len(subject.windows) > self.policy.max_windows:
            raise RasterThresholdSampleLimitError(
                "raster threshold window limit exceeded: "
                f"{len(subject.windows)} > {self.policy.max_windows}"
            )

        requested_cells = sum(window.cell_count for window in subject.windows)
        if requested_cells > self.policy.max_sample_cells:
            raise RasterThresholdSampleLimitError(
                "raster threshold sample-cell limit exceeded: "
                f"{requested_cells} > {self.policy.max_sample_cells}"
            )

        valid_samples = 0
        for window_index, window in enumerate(subject.windows):
            _validate_window_bounds(
                window=window,
                raster_height=self.source.height,
                raster_width=self.source.width,
            )
            values = self.source.read_window(window=window)
            _validate_window_values(window=window, values=values)

            for local_row, row_values in enumerate(values):
                for local_col, raw_value in enumerate(row_values):
                    row = window.row_off + local_row
                    col = window.col_off + local_col
                    if raw_value is None:
                        if self.policy.nodata_policy is RasterNoDataPolicy.REJECT:
                            return RasterThresholdHit(
                                reason=RasterThresholdHitReason.NODATA,
                                window_index=window_index,
                                row=row,
                                col=col,
                            )
                        continue

                    value = float(raw_value)
                    valid_samples += 1
                    if _violates_threshold(value=value, policy=self.policy):
                        return RasterThresholdHit(
                            reason=RasterThresholdHitReason.THRESHOLD,
                            window_index=window_index,
                            row=row,
                            col=col,
                            value=value,
                        )

        if valid_samples == 0:
            return RasterThresholdHit(reason=RasterThresholdHitReason.NO_VALID_SAMPLES)
        return None


class RasterThresholdConstraint:
    """HARD rule requiring bounded raster samples to satisfy a numeric threshold."""

    severity = ConstraintSeverity.HARD

    def __init__(
        self,
        *,
        scope: ConstraintScope,
        evaluator: RasterThresholdEvaluator,
        code: str = "raster.threshold",
    ) -> None:
        if not isinstance(evaluator, RasterThresholdEvaluator):
            raise RasterThresholdError("evaluator must be a RasterThresholdEvaluator")
        validate_constraint_metadata(code, self.severity, scope)
        self.code = code
        self.scope = scope
        self.evaluator = evaluator

    def evaluate(
        self,
        *,
        subject: RasterThresholdSubject,
        snapshot: TerritorySnapshot,
        context: RunContext,
    ) -> ConstraintResult:
        if snapshot.settings.working_srid != self.evaluator.working_srid:
            raise RasterThresholdError(
                "snapshot working_srid must match raster threshold source working_srid"
            )
        if context.working_srid != self.evaluator.working_srid:
            raise RasterThresholdError(
                "run context working_srid must match raster threshold source working_srid"
            )

        hit = self.evaluator.check(subject)
        if hit is None:
            return ConstraintResult(
                code=self.code,
                severity=self.severity,
                scope=self.scope,
                passed=True,
                message="all valid raster samples satisfy the configured threshold",
            )
        return ConstraintResult(
            code=self.code,
            severity=self.severity,
            scope=self.scope,
            passed=False,
            message=_failure_message(hit=hit, policy=self.evaluator.policy),
        )


def _validate_source_metadata(source: RasterThresholdSource) -> None:
    try:
        working_srid = source.working_srid
        width = source.width
        height = source.height
        read_window = source.read_window
    except AttributeError as exc:
        raise RasterThresholdError(
            "source must expose working_srid, width, height, and read_window"
        ) from exc
    require_working_crs(working_srid)
    _require_positive_int("source width", width)
    _require_positive_int("source height", height)
    if not callable(read_window):
        raise RasterThresholdError("source read_window must be callable")


def _validate_window_bounds(
    *,
    window: RasterWindow,
    raster_height: int,
    raster_width: int,
) -> None:
    if window.row_off + window.height > raster_height:
        raise RasterThresholdError("raster window exceeds source height")
    if window.col_off + window.width > raster_width:
        raise RasterThresholdError("raster window exceeds source width")


def _validate_window_values(
    *,
    window: RasterWindow,
    values: tuple[tuple[float | int | None, ...], ...],
) -> None:
    if not isinstance(values, tuple) or len(values) != window.height:
        raise RasterThresholdError("source returned an unexpected raster window height")
    for row in values:
        if not isinstance(row, tuple) or len(row) != window.width:
            raise RasterThresholdError("source returned an unexpected raster window width")
        for value in row:
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise RasterThresholdError("raster samples must be numeric or None")
            if not math.isfinite(float(value)):
                raise RasterThresholdError("raster samples must be finite or None")


def _violates_threshold(*, value: float, policy: RasterThresholdPolicy) -> bool:
    if policy.comparison is RasterThresholdComparison.AT_MOST:
        return value > policy.threshold
    return value < policy.threshold


def _require_positive_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise RasterThresholdError(f"{field_name} must be a positive integer")


def _failure_message(
    *,
    hit: RasterThresholdHit,
    policy: RasterThresholdPolicy,
) -> str:
    if hit.reason is RasterThresholdHitReason.NO_VALID_SAMPLES:
        return "raster threshold evaluation found no valid samples"
    if hit.reason is RasterThresholdHitReason.NODATA:
        return (
            f"raster nodata at row {hit.row}, col {hit.col} is rejected by policy"
        )
    comparator = "at most" if policy.comparison is RasterThresholdComparison.AT_MOST else "at least"
    return (
        f"raster value {hit.value:.6g} at row {hit.row}, col {hit.col} violates threshold; "
        f"requires {comparator} {policy.threshold:.6g}"
    )
