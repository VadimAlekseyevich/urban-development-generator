from __future__ import annotations

import math
import re
from dataclasses import dataclass

from core.urban_generator.zoning.config import ZoneClass, ZoningConfig
from core.urban_generator.zoning.partition import ZoningPartitionResult

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_AREA_TOLERANCE_RATIO = 1e-9
_MIN_AREA_TOLERANCE_M2 = 1e-6


class ZoneAssignmentError(ValueError):
    """Raised when functional zone assignment violates its deterministic contract."""


@dataclass(frozen=True, slots=True)
class ZoneAssignment:
    """One functional class assigned to an existing partition cell by index."""

    cell_index: int
    seed_index: int
    zone_class: ZoneClass
    area_m2: float
    suitability_score: float

    def __post_init__(self) -> None:
        _require_non_negative_int("cell_index", self.cell_index)
        _require_non_negative_int("seed_index", self.seed_index)
        if not isinstance(self.zone_class, ZoneClass):
            raise ZoneAssignmentError("zone_class must be a ZoneClass value")
        _require_positive_finite("area_m2", self.area_m2)
        score = _require_finite_number("suitability_score", self.suitability_score)
        if score < 0.0 or score > 1.0:
            raise ZoneAssignmentError("suitability_score must stay inside 0..1")


@dataclass(frozen=True, slots=True)
class ZoneShareDiagnostic:
    """Target-vs-achieved area allocation for one canonical zone class."""

    zone_class: ZoneClass
    target_share: float
    target_area_m2: float
    assigned_area_m2: float
    achieved_share: float
    absolute_area_error_m2: float
    cell_count: int

    def __post_init__(self) -> None:
        if not isinstance(self.zone_class, ZoneClass):
            raise ZoneAssignmentError("diagnostic zone_class must be a ZoneClass value")
        target_share = _require_finite_number("target_share", self.target_share)
        achieved_share = _require_finite_number("achieved_share", self.achieved_share)
        if target_share < 0.0 or target_share > 1.0:
            raise ZoneAssignmentError("target_share must stay inside 0..1")
        if achieved_share < 0.0 or achieved_share > 1.0:
            raise ZoneAssignmentError("achieved_share must stay inside 0..1")
        _require_non_negative_finite("target_area_m2", self.target_area_m2)
        _require_non_negative_finite("assigned_area_m2", self.assigned_area_m2)
        _require_non_negative_finite("absolute_area_error_m2", self.absolute_area_error_m2)
        _require_non_negative_int("cell_count", self.cell_count)
        if not math.isclose(
            self.absolute_area_error_m2,
            abs(self.assigned_area_m2 - self.target_area_m2),
            rel_tol=1e-12,
            abs_tol=1e-9,
        ):
            raise ZoneAssignmentError(
                "absolute_area_error_m2 must match the assigned-vs-target area difference"
            )


@dataclass(frozen=True, slots=True)
class ZoneAssignmentResult:
    """Geometry-independent zone labels plus target-share diagnostics."""

    assignments: tuple[ZoneAssignment, ...]
    shares: tuple[ZoneShareDiagnostic, ...]
    total_area_m2: float
    zoning_config_version: str
    zoning_config_fingerprint: str
    strategy_version: str

    def __post_init__(self) -> None:
        if not isinstance(self.assignments, tuple) or not self.assignments:
            raise ZoneAssignmentError("assignments must be a non-empty immutable tuple")
        if any(not isinstance(item, ZoneAssignment) for item in self.assignments):
            raise ZoneAssignmentError("assignments must contain only ZoneAssignment values")
        if tuple(item.cell_index for item in self.assignments) != tuple(
            range(len(self.assignments))
        ):
            raise ZoneAssignmentError(
                "assignments must contain every partition cell once in cell-index order"
            )
        if len({item.seed_index for item in self.assignments}) != len(self.assignments):
            raise ZoneAssignmentError("assignment seed_index values must be unique")

        if not isinstance(self.shares, tuple):
            raise ZoneAssignmentError("shares must be an immutable tuple")
        if any(not isinstance(item, ZoneShareDiagnostic) for item in self.shares):
            raise ZoneAssignmentError("shares must contain only ZoneShareDiagnostic values")
        if tuple(item.zone_class for item in self.shares) != tuple(ZoneClass):
            raise ZoneAssignmentError("shares must contain all canonical zone classes in order")

        total_area_m2 = _require_positive_finite("total_area_m2", self.total_area_m2)
        if not isinstance(self.zoning_config_version, str) or not self.zoning_config_version:
            raise ZoneAssignmentError("zoning_config_version must be a non-empty string")
        if (
            not isinstance(self.zoning_config_fingerprint, str)
            or _SHA256_RE.fullmatch(self.zoning_config_fingerprint) is None
        ):
            raise ZoneAssignmentError(
                "zoning_config_fingerprint must be a lowercase SHA-256 hex digest"
            )
        if not isinstance(self.strategy_version, str) or not self.strategy_version:
            raise ZoneAssignmentError("strategy_version must be a non-empty string")

        tolerance_m2 = max(
            _MIN_AREA_TOLERANCE_M2,
            total_area_m2 * _AREA_TOLERANCE_RATIO,
        )
        assigned_total = math.fsum(item.area_m2 for item in self.assignments)
        if not math.isclose(assigned_total, total_area_m2, rel_tol=0.0, abs_tol=tolerance_m2):
            raise ZoneAssignmentError("assignment areas must sum to total_area_m2")
        diagnostic_total = math.fsum(item.assigned_area_m2 for item in self.shares)
        if not math.isclose(
            diagnostic_total,
            total_area_m2,
            rel_tol=0.0,
            abs_tol=tolerance_m2,
        ):
            raise ZoneAssignmentError("diagnostic assigned areas must sum to total_area_m2")
        target_total = math.fsum(item.target_area_m2 for item in self.shares)
        if not math.isclose(target_total, total_area_m2, rel_tol=0.0, abs_tol=tolerance_m2):
            raise ZoneAssignmentError("diagnostic target areas must sum to total_area_m2")

    def assignment_for_cell(self, cell_index: int) -> ZoneAssignment:
        _require_non_negative_int("cell_index", cell_index)
        if cell_index >= len(self.assignments):
            raise ZoneAssignmentError(f"unknown partition cell index: {cell_index}")
        return self.assignments[cell_index]

    def share(self, zone_class: ZoneClass) -> ZoneShareDiagnostic:
        if not isinstance(zone_class, ZoneClass):
            raise ZoneAssignmentError("share lookup requires a ZoneClass value")
        return self.shares[tuple(ZoneClass).index(zone_class)]

    @property
    def absolute_target_error_m2(self) -> float:
        return math.fsum(item.absolute_area_error_m2 for item in self.shares)


class SuitabilityTargetShareAssigner:
    """Assign functional classes by suitability priority and remaining target-area deficit."""

    version = "1"

    def assign(
        self,
        *,
        partition: ZoningPartitionResult,
        config: ZoningConfig,
    ) -> ZoneAssignmentResult:
        if not isinstance(partition, ZoningPartitionResult):
            raise ZoneAssignmentError("partition must be a ZoningPartitionResult")
        if not isinstance(config, ZoningConfig):
            raise ZoneAssignmentError("config must be a ZoningConfig")

        total_area_m2 = partition.developable_area_m2
        target_area_by_class = {
            zone.zone_class: total_area_m2 * zone.target_share for zone in config.zones
        }
        assigned_area_by_class = {zone_class: 0.0 for zone_class in ZoneClass}
        assignment_by_cell: dict[int, ZoneAssignment] = {}

        prioritized_cells = sorted(
            enumerate(partition.cells),
            key=lambda item: (-item[1].seed.suitability_score, item[1].seed_index),
        )
        canonical_order = {zone_class: index for index, zone_class in enumerate(ZoneClass)}

        for cell_index, cell in prioritized_cells:
            selected_class = max(
                ZoneClass,
                key=lambda zone_class: (
                    target_area_by_class[zone_class] - assigned_area_by_class[zone_class],
                    config.zone(zone_class).target_share,
                    -canonical_order[zone_class],
                ),
            )
            assigned_area_by_class[selected_class] += cell.area_m2
            assignment_by_cell[cell_index] = ZoneAssignment(
                cell_index=cell_index,
                seed_index=cell.seed_index,
                zone_class=selected_class,
                area_m2=cell.area_m2,
                suitability_score=cell.seed.suitability_score,
            )

        assignments = tuple(assignment_by_cell[index] for index in range(len(partition.cells)))
        counts_by_class = {
            zone_class: sum(
                assignment.zone_class is zone_class for assignment in assignments
            )
            for zone_class in ZoneClass
        }
        shares = tuple(
            ZoneShareDiagnostic(
                zone_class=zone.zone_class,
                target_share=zone.target_share,
                target_area_m2=target_area_by_class[zone.zone_class],
                assigned_area_m2=assigned_area_by_class[zone.zone_class],
                achieved_share=assigned_area_by_class[zone.zone_class] / total_area_m2,
                absolute_area_error_m2=abs(
                    assigned_area_by_class[zone.zone_class]
                    - target_area_by_class[zone.zone_class]
                ),
                cell_count=counts_by_class[zone.zone_class],
            )
            for zone in config.zones
        )
        return ZoneAssignmentResult(
            assignments=assignments,
            shares=shares,
            total_area_m2=total_area_m2,
            zoning_config_version=config.version,
            zoning_config_fingerprint=config.fingerprint,
            strategy_version=self.version,
        )


def _require_non_negative_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ZoneAssignmentError(f"{field_name} must be a non-negative integer")


def _require_finite_number(field_name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ZoneAssignmentError(f"{field_name} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise ZoneAssignmentError(f"{field_name} must be a finite number")
    return number


def _require_positive_finite(field_name: str, value: float) -> float:
    number = _require_finite_number(field_name, value)
    if number <= 0.0:
        raise ZoneAssignmentError(f"{field_name} must be greater than zero")
    return number


def _require_non_negative_finite(field_name: str, value: float) -> float:
    number = _require_finite_number(field_name, value)
    if number < 0.0:
        raise ZoneAssignmentError(f"{field_name} must be non-negative")
    return number
