from __future__ import annotations

import math
import re
from dataclasses import dataclass

from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry

from core.urban_generator.buildings.attributes import AssignedBuildingAttributes
from core.urban_generator.domain.crs import require_working_crs

DEFAULT_MAX_BUILDING_AREA_SUBJECTS = 100_000
_COVERAGE_EPSILON_M2 = 1e-9
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,255}$")


class BuildingAreaMetricsError(ValueError):
    """Raised when S08-T11 area/GFA inputs violate the metric contract."""


@dataclass(frozen=True, slots=True)
class BuildingAreaSubject:
    """One assigned building footprint measured in the project working CRS."""

    building_id: str
    geometry: BaseGeometry
    attributes: AssignedBuildingAttributes
    working_srid: int

    def __post_init__(self) -> None:
        _require_id("building_id", self.building_id)
        require_working_crs(self.working_srid)
        _require_polygonal_geometry("building geometry", self.geometry)
        if not isinstance(self.attributes, AssignedBuildingAttributes):
            raise BuildingAreaMetricsError(
                "attributes must be AssignedBuildingAttributes"
            )
        if self.attributes.building_id != self.building_id:
            raise BuildingAreaMetricsError(
                "attribute building_id must match area subject building_id"
            )


@dataclass(frozen=True, slots=True)
class BuildingAreaBaseline:
    """Authoritative existing-state area/GFA included in aggregate metrics."""

    footprint_area_m2: float = 0.0
    gfa_m2: float = 0.0

    def __post_init__(self) -> None:
        footprint = _require_non_negative_finite(
            "footprint_area_m2",
            self.footprint_area_m2,
        )
        gfa = _require_non_negative_finite("gfa_m2", self.gfa_m2)
        object.__setattr__(self, "footprint_area_m2", footprint)
        object.__setattr__(self, "gfa_m2", gfa)


@dataclass(frozen=True, slots=True)
class BuildingAreaMetrics:
    """Authoritative raw footprint/GFA values for one generated building."""

    building_id: str
    footprint_area_m2: float
    floors: int
    gfa_m2: float

    def __post_init__(self) -> None:
        _require_id("building_id", self.building_id)
        footprint = _require_positive_finite(
            "footprint_area_m2",
            self.footprint_area_m2,
        )
        _require_positive_int("floors", self.floors)
        gfa = _require_positive_finite("gfa_m2", self.gfa_m2)
        expected = footprint * self.floors
        if not math.isclose(gfa, expected, rel_tol=1e-12, abs_tol=1e-9):
            raise BuildingAreaMetricsError(
                "gfa_m2 must equal footprint_area_m2 * floors"
            )
        object.__setattr__(self, "footprint_area_m2", footprint)
        object.__setattr__(self, "gfa_m2", gfa)


@dataclass(frozen=True, slots=True)
class BuildingAreaSummary:
    """Aggregate authoritative coverage/FAR metrics for one explicit site area."""

    site_area_m2: float
    baseline_footprint_area_m2: float
    baseline_gfa_m2: float
    generated_footprint_area_m2: float
    generated_gfa_m2: float
    total_footprint_area_m2: float
    total_gfa_m2: float
    coverage_ratio: float
    far: float
    building_count: int

    def __post_init__(self) -> None:
        site_area = _require_positive_finite("site_area_m2", self.site_area_m2)
        for field_name in (
            "baseline_footprint_area_m2",
            "baseline_gfa_m2",
            "generated_footprint_area_m2",
            "generated_gfa_m2",
            "total_footprint_area_m2",
            "total_gfa_m2",
            "coverage_ratio",
            "far",
        ):
            _require_non_negative_finite(field_name, getattr(self, field_name))
        _require_non_negative_int("building_count", self.building_count)

        expected_footprint = (
            self.baseline_footprint_area_m2
            + self.generated_footprint_area_m2
        )
        expected_gfa = self.baseline_gfa_m2 + self.generated_gfa_m2
        if not math.isclose(
            self.total_footprint_area_m2,
            expected_footprint,
            rel_tol=1e-12,
            abs_tol=1e-9,
        ):
            raise BuildingAreaMetricsError(
                "total_footprint_area_m2 must equal baseline + generated"
            )
        if not math.isclose(
            self.total_gfa_m2,
            expected_gfa,
            rel_tol=1e-12,
            abs_tol=1e-9,
        ):
            raise BuildingAreaMetricsError(
                "total_gfa_m2 must equal baseline + generated"
            )
        if self.total_footprint_area_m2 > site_area + _COVERAGE_EPSILON_M2:
            raise BuildingAreaMetricsError(
                "total footprint area cannot exceed site area"
            )
        if not math.isclose(
            self.coverage_ratio,
            self.total_footprint_area_m2 / site_area,
            rel_tol=1e-12,
            abs_tol=1e-12,
        ):
            raise BuildingAreaMetricsError(
                "coverage_ratio must equal total footprint area / site area"
            )
        if not math.isclose(
            self.far,
            self.total_gfa_m2 / site_area,
            rel_tol=1e-12,
            abs_tol=1e-12,
        ):
            raise BuildingAreaMetricsError(
                "far must equal total GFA / site area"
            )
        object.__setattr__(self, "site_area_m2", site_area)


@dataclass(frozen=True, slots=True)
class BuildingAreaCalculationResult:
    """Canonical T11 per-building and aggregate area metrics."""

    working_srid: int
    buildings: tuple[BuildingAreaMetrics, ...]
    summary: BuildingAreaSummary

    def __post_init__(self) -> None:
        require_working_crs(self.working_srid)
        if not isinstance(self.buildings, tuple):
            raise BuildingAreaMetricsError(
                "buildings must be an immutable tuple"
            )
        if any(
            not isinstance(item, BuildingAreaMetrics)
            for item in self.buildings
        ):
            raise BuildingAreaMetricsError(
                "buildings must contain BuildingAreaMetrics values"
            )
        ids = tuple(item.building_id for item in self.buildings)
        if ids != tuple(sorted(ids)) or len(ids) != len(set(ids)):
            raise BuildingAreaMetricsError(
                "building metric ids must be sorted and unique"
            )
        if not isinstance(self.summary, BuildingAreaSummary):
            raise BuildingAreaMetricsError(
                "summary must be a BuildingAreaSummary"
            )
        if self.summary.building_count != len(self.buildings):
            raise BuildingAreaMetricsError(
                "summary building_count must match building metrics"
            )


class BuildingAreaMetricsCalculator:
    """Calculate bounded footprint/GFA/coverage/FAR in one working CRS."""

    def __init__(
        self,
        *,
        working_srid: int,
        max_subjects: int = DEFAULT_MAX_BUILDING_AREA_SUBJECTS,
    ) -> None:
        self.working_crs = require_working_crs(working_srid)
        _require_positive_int("max_subjects", max_subjects)
        self.max_subjects = max_subjects

    def calculate(
        self,
        subjects: tuple[BuildingAreaSubject, ...],
        *,
        site_area_m2: float,
        baseline: BuildingAreaBaseline | None = None,
    ) -> BuildingAreaCalculationResult:
        resolved_baseline = (
            baseline if baseline is not None else BuildingAreaBaseline()
        )
        self._validate_inputs(
            subjects=subjects,
            baseline=resolved_baseline,
        )
        site_area = _require_positive_finite("site_area_m2", site_area_m2)

        measured: list[BuildingAreaMetrics] = []
        for subject in sorted(subjects, key=lambda item: item.building_id):
            footprint_area = float(subject.geometry.area)
            _require_positive_finite("building footprint area", footprint_area)
            floors = subject.attributes.floors
            gfa = footprint_area * floors
            _require_positive_finite("building GFA", gfa)
            measured.append(
                BuildingAreaMetrics(
                    building_id=subject.building_id,
                    footprint_area_m2=footprint_area,
                    floors=floors,
                    gfa_m2=gfa,
                )
            )

        buildings = tuple(measured)
        generated_footprint = math.fsum(
            item.footprint_area_m2 for item in buildings
        )
        generated_gfa = math.fsum(item.gfa_m2 for item in buildings)
        total_footprint = (
            resolved_baseline.footprint_area_m2 + generated_footprint
        )
        total_gfa = resolved_baseline.gfa_m2 + generated_gfa
        if total_footprint > site_area + _COVERAGE_EPSILON_M2:
            raise BuildingAreaMetricsError(
                "total footprint area cannot exceed site area"
            )

        summary = BuildingAreaSummary(
            site_area_m2=site_area,
            baseline_footprint_area_m2=resolved_baseline.footprint_area_m2,
            baseline_gfa_m2=resolved_baseline.gfa_m2,
            generated_footprint_area_m2=generated_footprint,
            generated_gfa_m2=generated_gfa,
            total_footprint_area_m2=total_footprint,
            total_gfa_m2=total_gfa,
            coverage_ratio=total_footprint / site_area,
            far=total_gfa / site_area,
            building_count=len(buildings),
        )
        return BuildingAreaCalculationResult(
            working_srid=self.working_crs.srid,
            buildings=buildings,
            summary=summary,
        )

    def _validate_inputs(
        self,
        *,
        subjects: tuple[BuildingAreaSubject, ...],
        baseline: BuildingAreaBaseline,
    ) -> None:
        if not isinstance(subjects, tuple):
            raise BuildingAreaMetricsError(
                "subjects must be an immutable tuple"
            )
        if len(subjects) > self.max_subjects:
            raise BuildingAreaMetricsError(
                "building area subject limit exceeded: "
                f"{len(subjects)} > {self.max_subjects}"
            )
        if any(not isinstance(item, BuildingAreaSubject) for item in subjects):
            raise BuildingAreaMetricsError(
                "subjects must contain BuildingAreaSubject values"
            )
        if any(
            item.working_srid != self.working_crs.srid
            for item in subjects
        ):
            raise BuildingAreaMetricsError(
                "subject working_srid must match calculator working CRS"
            )
        ids = tuple(item.building_id for item in subjects)
        if len(ids) != len(set(ids)):
            raise BuildingAreaMetricsError(
                "building ids must be unique"
            )
        if not isinstance(baseline, BuildingAreaBaseline):
            raise BuildingAreaMetricsError(
                "baseline must be a BuildingAreaBaseline"
            )


def _require_polygonal_geometry(
    field_name: str,
    geometry: BaseGeometry,
) -> None:
    if not isinstance(geometry, (Polygon, MultiPolygon)):
        raise BuildingAreaMetricsError(
            f"{field_name} must be Polygon or MultiPolygon"
        )
    if geometry.is_empty or not geometry.is_valid or geometry.has_z:
        raise BuildingAreaMetricsError(
            f"{field_name} must be non-empty, valid and 2D"
        )
    _require_positive_finite(f"{field_name} area", float(geometry.area))


def _require_id(field_name: str, value: str) -> None:
    if not isinstance(value, str) or _ID_RE.fullmatch(value) is None:
        raise BuildingAreaMetricsError(
            f"invalid {field_name}: {value!r}"
        )


def _require_positive_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise BuildingAreaMetricsError(
            f"{field_name} must be a positive integer"
        )


def _require_non_negative_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise BuildingAreaMetricsError(
            f"{field_name} must be a non-negative integer"
        )


def _require_non_negative_finite(field_name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BuildingAreaMetricsError(
            f"{field_name} must be a finite non-negative number"
        )
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        raise BuildingAreaMetricsError(
            f"{field_name} must be a finite non-negative number"
        )
    return number


def _require_positive_finite(field_name: str, value: float) -> float:
    number = _require_non_negative_finite(field_name, value)
    if number <= 0.0:
        raise BuildingAreaMetricsError(
            f"{field_name} must be greater than zero"
        )
    return number
