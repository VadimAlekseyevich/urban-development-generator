from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from shapely.geometry import GeometryCollection, MultiPolygon, Point, Polygon, box
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from core.urban_generator.domain import require_working_crs
from core.urban_generator.infrastructure.candidates import (
    InfrastructureCandidateSiteResult,
    InfrastructureSiteCandidate,
)
from core.urban_generator.infrastructure.config import (
    InfrastructureCandidateSource,
    InfrastructureType,
)
from core.urban_generator.zoning import ZoneClass

DEFAULT_SITE_GEOMETRY_SEARCH_ITERATIONS = 64


class InfrastructureCandidateGeometryError(ValueError):
    """Raised when S10-T05 facility candidate geometry violates the contract."""


class InfrastructureCandidateGeometryKind(StrEnum):
    """How a generated facility candidate is spatially represented."""

    SITE = "site"
    HOST_BUILDING = "host_building"


@dataclass(frozen=True, slots=True)
class InfrastructureCandidateGeometry:
    """Generated facility geometry or an explicit host-building reference."""

    candidate_id: str
    infrastructure_type_code: str
    source_kind: InfrastructureCandidateSource
    source_id: str
    zone_class: ZoneClass
    working_srid: int
    anchor: Point
    kind: InfrastructureCandidateGeometryKind
    site_geometry: BaseGeometry | None = None
    site_area_m2: float | None = None
    host_building_id: str | None = None
    zone_id: str | None = None
    block_id: str | None = None

    def __post_init__(self) -> None:
        _require_id("candidate_id", self.candidate_id)
        _require_id(
            "infrastructure_type_code",
            self.infrastructure_type_code,
        )
        if not isinstance(self.source_kind, InfrastructureCandidateSource):
            raise InfrastructureCandidateGeometryError(
                "source_kind must be InfrastructureCandidateSource"
            )
        _require_id("source_id", self.source_id)
        if not isinstance(self.zone_class, ZoneClass):
            raise InfrastructureCandidateGeometryError(
                "zone_class must be a ZoneClass"
            )
        require_working_crs(self.working_srid)
        if not isinstance(self.anchor, Point) or self.anchor.is_empty:
            raise InfrastructureCandidateGeometryError(
                "anchor must be a non-empty Point"
            )
        if self.anchor.has_z:
            raise InfrastructureCandidateGeometryError(
                "anchor must be 2D"
            )
        if not isinstance(self.kind, InfrastructureCandidateGeometryKind):
            raise InfrastructureCandidateGeometryError(
                "kind must be InfrastructureCandidateGeometryKind"
            )
        if self.zone_id is not None:
            _require_id("zone_id", self.zone_id)
        if self.block_id is not None:
            _require_id("block_id", self.block_id)

        if self.kind is InfrastructureCandidateGeometryKind.SITE:
            if self.source_kind is InfrastructureCandidateSource.BUILDING:
                raise InfrastructureCandidateGeometryError(
                    "building candidate must use explicit host building"
                )
            if self.host_building_id is not None:
                raise InfrastructureCandidateGeometryError(
                    "site candidate must not set host_building_id"
                )
            if self.site_geometry is None or self.site_area_m2 is None:
                raise InfrastructureCandidateGeometryError(
                    "site candidate requires site_geometry and site_area_m2"
                )
            _require_polygonal_geometry("site_geometry", self.site_geometry)
            area = _require_positive_finite(
                "site_area_m2",
                self.site_area_m2,
            )
            if not math.isclose(
                area,
                float(self.site_geometry.area),
                rel_tol=1e-12,
                abs_tol=1e-6,
            ):
                raise InfrastructureCandidateGeometryError(
                    "site_area_m2 must match site_geometry area"
                )
            if not self.site_geometry.covers(self.anchor):
                raise InfrastructureCandidateGeometryError(
                    "site_geometry must cover candidate anchor"
                )
            object.__setattr__(self, "site_area_m2", area)
        else:
            if self.source_kind is not InfrastructureCandidateSource.BUILDING:
                raise InfrastructureCandidateGeometryError(
                    "host-building geometry requires building source"
                )
            if self.host_building_id != self.source_id:
                raise InfrastructureCandidateGeometryError(
                    "host_building_id must equal building source_id"
                )
            if self.site_geometry is not None or self.site_area_m2 is not None:
                raise InfrastructureCandidateGeometryError(
                    "host-building candidate must not duplicate site geometry"
                )


@dataclass(frozen=True, slots=True)
class InfrastructureCandidateGeometryDiagnostics:
    candidate_count: int
    polygon_site_count: int
    host_building_count: int
    clipped_site_count: int
    full_source_site_count: int

    def __post_init__(self) -> None:
        for field_name in (
            "candidate_count",
            "polygon_site_count",
            "host_building_count",
            "clipped_site_count",
            "full_source_site_count",
        ):
            _require_non_negative_int(field_name, getattr(self, field_name))
        if (
            self.polygon_site_count + self.host_building_count
            != self.candidate_count
        ):
            raise InfrastructureCandidateGeometryError(
                "site and host-building counts must equal candidate_count"
            )
        if (
            self.clipped_site_count + self.full_source_site_count
            != self.polygon_site_count
        ):
            raise InfrastructureCandidateGeometryError(
                "clipped and full-source counts must equal polygon_site_count"
            )


@dataclass(frozen=True, slots=True)
class InfrastructureCandidateGeometryResult:
    infrastructure_type_code: str
    working_srid: int
    candidates: tuple[InfrastructureCandidateGeometry, ...]
    diagnostics: InfrastructureCandidateGeometryDiagnostics

    def __post_init__(self) -> None:
        _require_id(
            "infrastructure_type_code",
            self.infrastructure_type_code,
        )
        require_working_crs(self.working_srid)
        if not isinstance(self.candidates, tuple):
            raise InfrastructureCandidateGeometryError(
                "candidates must be an immutable tuple"
            )
        if any(
            not isinstance(item, InfrastructureCandidateGeometry)
            for item in self.candidates
        ):
            raise InfrastructureCandidateGeometryError(
                "candidates must contain InfrastructureCandidateGeometry values"
            )
        ids = tuple(item.candidate_id for item in self.candidates)
        if ids != tuple(sorted(ids)) or len(ids) != len(set(ids)):
            raise InfrastructureCandidateGeometryError(
                "candidate geometries must be sorted and unique"
            )
        if any(
            item.infrastructure_type_code != self.infrastructure_type_code
            for item in self.candidates
        ):
            raise InfrastructureCandidateGeometryError(
                "candidate infrastructure type must match result"
            )
        if any(
            item.working_srid != self.working_srid
            for item in self.candidates
        ):
            raise InfrastructureCandidateGeometryError(
                "candidate working_srid must match result"
            )
        if not isinstance(
            self.diagnostics,
            InfrastructureCandidateGeometryDiagnostics,
        ):
            raise InfrastructureCandidateGeometryError(
                "diagnostics must be InfrastructureCandidateGeometryDiagnostics"
            )
        if self.diagnostics.candidate_count != len(self.candidates):
            raise InfrastructureCandidateGeometryError(
                "diagnostic candidate_count must match candidates"
            )


class InfrastructureCandidateGeometryBuilder:
    """Materialize polygon sites or explicit host-building refs from T04 candidates."""

    def __init__(
        self,
        *,
        search_iterations: int = DEFAULT_SITE_GEOMETRY_SEARCH_ITERATIONS,
    ) -> None:
        _require_positive_int("search_iterations", search_iterations)
        self.search_iterations = search_iterations

    def build(
        self,
        candidates: InfrastructureCandidateSiteResult,
        *,
        infrastructure_type: InfrastructureType,
    ) -> InfrastructureCandidateGeometryResult:
        if not isinstance(candidates, InfrastructureCandidateSiteResult):
            raise InfrastructureCandidateGeometryError(
                "candidates must be InfrastructureCandidateSiteResult"
            )
        if not isinstance(infrastructure_type, InfrastructureType):
            raise InfrastructureCandidateGeometryError(
                "infrastructure_type must be InfrastructureType"
            )
        if candidates.infrastructure_type_code != infrastructure_type.code:
            raise InfrastructureCandidateGeometryError(
                "candidate result infrastructure type must match InfrastructureType"
            )

        output: list[InfrastructureCandidateGeometry] = []
        clipped_count = 0
        full_source_count = 0
        host_count = 0

        for candidate in candidates.candidates:
            if candidate.source_kind is InfrastructureCandidateSource.BUILDING:
                output.append(
                    self._host_building(candidate)
                )
                host_count += 1
                continue

            site, clipped = self._site_geometry(
                candidate,
                target_area_m2=infrastructure_type.target_site_area_m2,
            )
            output.append(
                InfrastructureCandidateGeometry(
                    candidate_id=candidate.candidate_id,
                    infrastructure_type_code=(
                        candidate.infrastructure_type_code
                    ),
                    source_kind=candidate.source_kind,
                    source_id=candidate.source_id,
                    zone_class=candidate.zone_class,
                    working_srid=candidate.working_srid,
                    anchor=candidate.anchor,
                    kind=InfrastructureCandidateGeometryKind.SITE,
                    site_geometry=site,
                    site_area_m2=float(site.area),
                    zone_id=candidate.zone_id,
                    block_id=candidate.block_id,
                )
            )
            if clipped:
                clipped_count += 1
            else:
                full_source_count += 1

        ordered = tuple(sorted(output, key=lambda item: item.candidate_id))
        return InfrastructureCandidateGeometryResult(
            infrastructure_type_code=infrastructure_type.code,
            working_srid=candidates.working_srid,
            candidates=ordered,
            diagnostics=InfrastructureCandidateGeometryDiagnostics(
                candidate_count=len(ordered),
                polygon_site_count=len(ordered) - host_count,
                host_building_count=host_count,
                clipped_site_count=clipped_count,
                full_source_site_count=full_source_count,
            ),
        )

    @staticmethod
    def _host_building(
        candidate: InfrastructureSiteCandidate,
    ) -> InfrastructureCandidateGeometry:
        return InfrastructureCandidateGeometry(
            candidate_id=candidate.candidate_id,
            infrastructure_type_code=candidate.infrastructure_type_code,
            source_kind=candidate.source_kind,
            source_id=candidate.source_id,
            zone_class=candidate.zone_class,
            working_srid=candidate.working_srid,
            anchor=candidate.anchor,
            kind=InfrastructureCandidateGeometryKind.HOST_BUILDING,
            host_building_id=candidate.source_id,
            zone_id=candidate.zone_id,
            block_id=candidate.block_id,
        )

    def _site_geometry(
        self,
        candidate: InfrastructureSiteCandidate,
        *,
        target_area_m2: float,
    ) -> tuple[BaseGeometry, bool]:
        source = candidate.source_geometry
        _require_polygonal_geometry("candidate source_geometry", source)
        source_area = float(source.area)
        target = min(
            _require_positive_finite(
                "target_site_area_m2",
                target_area_m2,
            ),
            source_area,
        )
        if math.isclose(
            target,
            source_area,
            rel_tol=1e-12,
            abs_tol=1e-6,
        ):
            return source, False

        anchor = candidate.anchor
        min_x, min_y, max_x, max_y = source.bounds
        high = max(
            abs(anchor.x - min_x),
            abs(anchor.x - max_x),
            abs(anchor.y - min_y),
            abs(anchor.y - max_y),
        )
        if not math.isfinite(high) or high <= 0.0:
            raise InfrastructureCandidateGeometryError(
                "candidate source bounds cannot define a site search window"
            )

        low = 0.0
        for _ in range(self.search_iterations):
            half = (low + high) / 2.0
            clipped = source.intersection(
                box(
                    anchor.x - half,
                    anchor.y - half,
                    anchor.x + half,
                    anchor.y + half,
                )
            )
            area = float(clipped.area)
            if area < target:
                low = half
            else:
                high = half

        site = _polygonal_part(
            source.intersection(
                box(
                    anchor.x - high,
                    anchor.y - high,
                    anchor.x + high,
                    anchor.y + high,
                )
            )
        )
        if not site.covers(anchor):
            raise InfrastructureCandidateGeometryError(
                "generated site must cover candidate anchor"
            )
        if not math.isclose(
            float(site.area),
            target,
            rel_tol=1e-6,
            abs_tol=1e-4,
        ):
            raise InfrastructureCandidateGeometryError(
                "generated site area did not converge to target area"
            )
        if not source.covers(site):
            raise InfrastructureCandidateGeometryError(
                "generated site must stay inside source geometry"
            )
        return site, True


def _polygonal_part(geometry: BaseGeometry) -> BaseGeometry:
    if isinstance(geometry, (Polygon, MultiPolygon)):
        _require_polygonal_geometry("generated site", geometry)
        return geometry
    if not isinstance(geometry, GeometryCollection):
        raise InfrastructureCandidateGeometryError(
            "generated site intersection is not polygonal"
        )

    polygons: list[Polygon] = []
    for item in geometry.geoms:
        if isinstance(item, Polygon):
            polygons.append(item)
        elif isinstance(item, MultiPolygon):
            polygons.extend(item.geoms)
    if not polygons:
        raise InfrastructureCandidateGeometryError(
            "generated site intersection has no polygonal area"
        )
    merged = unary_union(polygons)
    if not isinstance(merged, (Polygon, MultiPolygon)):
        raise InfrastructureCandidateGeometryError(
            "generated site polygon merge failed"
        )
    _require_polygonal_geometry("generated site", merged)
    return merged


def _require_polygonal_geometry(
    field_name: str,
    geometry: BaseGeometry,
) -> None:
    if not isinstance(geometry, (Polygon, MultiPolygon)):
        raise InfrastructureCandidateGeometryError(
            f"{field_name} must be Polygon or MultiPolygon"
        )
    if geometry.is_empty or not geometry.is_valid or geometry.has_z:
        raise InfrastructureCandidateGeometryError(
            f"{field_name} must be non-empty, valid and 2D"
        )
    area = float(geometry.area)
    if not math.isfinite(area) or area <= 0.0:
        raise InfrastructureCandidateGeometryError(
            f"{field_name} must have positive finite area"
        )


def _require_id(field_name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise InfrastructureCandidateGeometryError(
            f"{field_name} must be a non-empty string"
        )
    if "\n" in value or "\r" in value:
        raise InfrastructureCandidateGeometryError(
            f"{field_name} must not contain line breaks"
        )


def _require_positive_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise InfrastructureCandidateGeometryError(
            f"{field_name} must be a positive integer"
        )


def _require_non_negative_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise InfrastructureCandidateGeometryError(
            f"{field_name} must be a non-negative integer"
        )


def _require_positive_finite(
    field_name: str,
    value: int | float,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InfrastructureCandidateGeometryError(
            f"{field_name} must be a finite positive number"
        )
    number = float(value)
    if not math.isfinite(number) or number <= 0.0:
        raise InfrastructureCandidateGeometryError(
            f"{field_name} must be a finite positive number"
        )
    return number
