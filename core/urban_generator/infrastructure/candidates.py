from __future__ import annotations

import math
from dataclasses import dataclass

from shapely.geometry import MultiPolygon, Point, Polygon
from shapely.geometry.base import BaseGeometry

from core.urban_generator.blocks import (
    BlockZoneAssociationResult,
    BlockZoneAssociationStatus,
    PlanningParcel,
)
from core.urban_generator.buildings import BuildingAreaSubject
from core.urban_generator.domain import require_working_crs
from core.urban_generator.infrastructure.config import (
    InfrastructureCandidateSource,
    InfrastructureType,
)
from core.urban_generator.zoning import ZoneClass

DEFAULT_MAX_INFRASTRUCTURE_CANDIDATES = 100_000
DEFAULT_MAX_INFRASTRUCTURE_CANDIDATE_INPUTS = 1_000_000


class InfrastructureCandidateSiteError(ValueError):
    """Raised when S10-T04 candidate generation violates its bounded contract."""


@dataclass(frozen=True, slots=True)
class InfrastructureSiteCandidate:
    """One deterministic source-backed facility candidate before T05 geometry."""

    candidate_id: str
    infrastructure_type_code: str
    source_kind: InfrastructureCandidateSource
    source_id: str
    zone_class: ZoneClass
    working_srid: int
    source_geometry: BaseGeometry
    available_area_m2: float
    anchor: Point
    zone_id: str | None = None
    block_id: str | None = None

    def __post_init__(self) -> None:
        _require_id("candidate_id", self.candidate_id)
        _require_id(
            "infrastructure_type_code",
            self.infrastructure_type_code,
        )
        if not isinstance(self.source_kind, InfrastructureCandidateSource):
            raise InfrastructureCandidateSiteError(
                "source_kind must be InfrastructureCandidateSource"
            )
        _require_id("source_id", self.source_id)
        if not isinstance(self.zone_class, ZoneClass):
            raise InfrastructureCandidateSiteError(
                "zone_class must be a ZoneClass"
            )
        require_working_crs(self.working_srid)
        _require_polygonal_geometry(
            "source_geometry",
            self.source_geometry,
        )
        area = _require_positive_finite(
            "available_area_m2",
            self.available_area_m2,
        )
        actual_area = float(self.source_geometry.area)
        if not math.isclose(
            area,
            actual_area,
            rel_tol=1e-12,
            abs_tol=1e-6,
        ):
            raise InfrastructureCandidateSiteError(
                "available_area_m2 must match source_geometry area"
            )
        if not isinstance(self.anchor, Point):
            raise InfrastructureCandidateSiteError(
                "anchor must be a Point"
            )
        if self.anchor.is_empty or self.anchor.has_z:
            raise InfrastructureCandidateSiteError(
                "anchor must be a non-empty 2D Point"
            )
        if not self.source_geometry.covers(self.anchor):
            raise InfrastructureCandidateSiteError(
                "source_geometry must cover candidate anchor"
            )
        if self.zone_id is not None:
            _require_id("zone_id", self.zone_id)
        if self.block_id is not None:
            _require_id("block_id", self.block_id)

        if self.source_kind is InfrastructureCandidateSource.BLOCK:
            if self.block_id != self.source_id:
                raise InfrastructureCandidateSiteError(
                    "block candidate block_id must equal source_id"
                )
            if self.zone_id is None:
                raise InfrastructureCandidateSiteError(
                    "block candidate requires zone_id"
                )
        elif self.source_kind is InfrastructureCandidateSource.PARCEL:
            if self.block_id is None or self.zone_id is None:
                raise InfrastructureCandidateSiteError(
                    "parcel candidate requires block_id and zone_id"
                )
        elif self.source_kind is InfrastructureCandidateSource.BUILDING:
            if self.block_id is not None:
                raise InfrastructureCandidateSiteError(
                    "building candidate must not invent block_id"
                )

        object.__setattr__(self, "available_area_m2", area)


@dataclass(frozen=True, slots=True)
class InfrastructureCandidateSiteDiagnostics:
    block_input_count: int
    parcel_input_count: int
    building_input_count: int
    candidate_count: int
    skipped_unzoned_count: int
    skipped_disallowed_zone_count: int

    def __post_init__(self) -> None:
        for field_name in (
            "block_input_count",
            "parcel_input_count",
            "building_input_count",
            "candidate_count",
            "skipped_unzoned_count",
            "skipped_disallowed_zone_count",
        ):
            _require_non_negative_int(field_name, getattr(self, field_name))


@dataclass(frozen=True, slots=True)
class InfrastructureCandidateSiteResult:
    infrastructure_type_code: str
    working_srid: int
    candidates: tuple[InfrastructureSiteCandidate, ...]
    diagnostics: InfrastructureCandidateSiteDiagnostics

    def __post_init__(self) -> None:
        _require_id(
            "infrastructure_type_code",
            self.infrastructure_type_code,
        )
        require_working_crs(self.working_srid)
        if not isinstance(self.candidates, tuple):
            raise InfrastructureCandidateSiteError(
                "candidates must be an immutable tuple"
            )
        if any(
            not isinstance(item, InfrastructureSiteCandidate)
            for item in self.candidates
        ):
            raise InfrastructureCandidateSiteError(
                "candidates must contain InfrastructureSiteCandidate values"
            )
        ids = tuple(item.candidate_id for item in self.candidates)
        if ids != tuple(sorted(ids)) or len(ids) != len(set(ids)):
            raise InfrastructureCandidateSiteError(
                "candidate ids must be sorted and unique"
            )
        if any(
            item.infrastructure_type_code != self.infrastructure_type_code
            for item in self.candidates
        ):
            raise InfrastructureCandidateSiteError(
                "candidate infrastructure type must match result"
            )
        if any(
            item.working_srid != self.working_srid
            for item in self.candidates
        ):
            raise InfrastructureCandidateSiteError(
                "candidate working_srid must match result"
            )
        if not isinstance(
            self.diagnostics,
            InfrastructureCandidateSiteDiagnostics,
        ):
            raise InfrastructureCandidateSiteError(
                "diagnostics must be InfrastructureCandidateSiteDiagnostics"
            )
        if self.diagnostics.candidate_count != len(self.candidates):
            raise InfrastructureCandidateSiteError(
                "diagnostic candidate_count must match candidates"
            )


class InfrastructureCandidateSiteGenerator:
    """Generate bounded source-backed candidates with explicit zone filtering."""

    def __init__(
        self,
        *,
        working_srid: int,
        max_candidates: int = DEFAULT_MAX_INFRASTRUCTURE_CANDIDATES,
        max_inputs: int = DEFAULT_MAX_INFRASTRUCTURE_CANDIDATE_INPUTS,
    ) -> None:
        self.working_crs = require_working_crs(working_srid)
        _require_positive_int("max_candidates", max_candidates)
        _require_positive_int("max_inputs", max_inputs)
        self.max_candidates = max_candidates
        self.max_inputs = max_inputs

    def generate(
        self,
        infrastructure_type: InfrastructureType,
        *,
        zoned_blocks: BlockZoneAssociationResult | None = None,
        parcels: tuple[PlanningParcel, ...] = (),
        buildings: tuple[BuildingAreaSubject, ...] = (),
    ) -> InfrastructureCandidateSiteResult:
        if not isinstance(infrastructure_type, InfrastructureType):
            raise InfrastructureCandidateSiteError(
                "infrastructure_type must be InfrastructureType"
            )
        if not isinstance(parcels, tuple):
            raise InfrastructureCandidateSiteError(
                "parcels must be an immutable tuple"
            )
        if not isinstance(buildings, tuple):
            raise InfrastructureCandidateSiteError(
                "buildings must be an immutable tuple"
            )
        if zoned_blocks is not None and not isinstance(
            zoned_blocks,
            BlockZoneAssociationResult,
        ):
            raise InfrastructureCandidateSiteError(
                "zoned_blocks must be BlockZoneAssociationResult or None"
            )

        block_count = (
            len(zoned_blocks.blocks)
            if zoned_blocks is not None
            else 0
        )
        total_inputs = block_count + len(parcels) + len(buildings)
        if total_inputs > self.max_inputs:
            raise InfrastructureCandidateSiteError(
                "infrastructure candidate input limit exceeded: "
                f"{total_inputs} > {self.max_inputs}"
            )

        drafts: list[InfrastructureSiteCandidate] = []
        skipped_unzoned = 0
        skipped_disallowed = 0
        allowed_zones = set(infrastructure_type.allowed_zones)
        enabled_sources = set(infrastructure_type.candidate_policy.sources)

        if InfrastructureCandidateSource.BLOCK in enabled_sources:
            if zoned_blocks is not None:
                if zoned_blocks.working_crs != self.working_crs:
                    raise InfrastructureCandidateSiteError(
                        "block working CRS must match candidate generator"
                    )
                seen_ids: set[str] = set()
                for item in sorted(
                    zoned_blocks.blocks,
                    key=lambda value: value.cleaned_block.block_id,
                ):
                    block = item.cleaned_block
                    if block.block_id in seen_ids:
                        raise InfrastructureCandidateSiteError(
                            f"duplicate block source id: {block.block_id}"
                        )
                    seen_ids.add(block.block_id)
                    association = item.association
                    if (
                        association.status
                        is not BlockZoneAssociationStatus.ASSOCIATED
                        or association.zone_id is None
                        or association.zone_class is None
                    ):
                        skipped_unzoned += 1
                        continue
                    if association.zone_class not in allowed_zones:
                        skipped_disallowed += 1
                        continue
                    drafts.append(
                        self._candidate(
                            infrastructure_type=infrastructure_type,
                            source_kind=InfrastructureCandidateSource.BLOCK,
                            source_id=block.block_id,
                            geometry=block.geometry,
                            zone_class=association.zone_class,
                            zone_id=association.zone_id,
                            block_id=block.block_id,
                        )
                    )

        if InfrastructureCandidateSource.PARCEL in enabled_sources:
            seen_ids = set()
            for parcel in sorted(parcels, key=lambda item: item.parcel_id):
                if not isinstance(parcel, PlanningParcel):
                    raise InfrastructureCandidateSiteError(
                        "parcels must contain PlanningParcel values"
                    )
                if parcel.working_srid != self.working_crs.srid:
                    raise InfrastructureCandidateSiteError(
                        "parcel working_srid must match candidate generator"
                    )
                if parcel.parcel_id in seen_ids:
                    raise InfrastructureCandidateSiteError(
                        f"duplicate parcel source id: {parcel.parcel_id}"
                    )
                seen_ids.add(parcel.parcel_id)
                if parcel.zone_id is None or parcel.zone_class is None:
                    skipped_unzoned += 1
                    continue
                if parcel.zone_class not in allowed_zones:
                    skipped_disallowed += 1
                    continue
                drafts.append(
                    self._candidate(
                        infrastructure_type=infrastructure_type,
                        source_kind=InfrastructureCandidateSource.PARCEL,
                        source_id=parcel.parcel_id,
                        geometry=parcel.buildable_envelope,
                        zone_class=parcel.zone_class,
                        zone_id=parcel.zone_id,
                        block_id=parcel.block_id,
                    )
                )

        if InfrastructureCandidateSource.BUILDING in enabled_sources:
            seen_ids = set()
            for building in sorted(
                buildings,
                key=lambda item: item.building_id,
            ):
                if not isinstance(building, BuildingAreaSubject):
                    raise InfrastructureCandidateSiteError(
                        "buildings must contain BuildingAreaSubject values"
                    )
                if building.working_srid != self.working_crs.srid:
                    raise InfrastructureCandidateSiteError(
                        "building working_srid must match candidate generator"
                    )
                if building.building_id in seen_ids:
                    raise InfrastructureCandidateSiteError(
                        f"duplicate building source id: {building.building_id}"
                    )
                seen_ids.add(building.building_id)
                zone_class = building.attributes.zone_class
                if zone_class not in allowed_zones:
                    skipped_disallowed += 1
                    continue
                drafts.append(
                    self._candidate(
                        infrastructure_type=infrastructure_type,
                        source_kind=InfrastructureCandidateSource.BUILDING,
                        source_id=building.building_id,
                        geometry=building.geometry,
                        zone_class=zone_class,
                        zone_id=None,
                        block_id=None,
                    )
                )

        ordered = tuple(sorted(drafts, key=lambda item: item.candidate_id))
        if len(ordered) > self.max_candidates:
            raise InfrastructureCandidateSiteError(
                "infrastructure candidate limit exceeded: "
                f"{len(ordered)} > {self.max_candidates}"
            )
        return InfrastructureCandidateSiteResult(
            infrastructure_type_code=infrastructure_type.code,
            working_srid=self.working_crs.srid,
            candidates=ordered,
            diagnostics=InfrastructureCandidateSiteDiagnostics(
                block_input_count=block_count,
                parcel_input_count=len(parcels),
                building_input_count=len(buildings),
                candidate_count=len(ordered),
                skipped_unzoned_count=skipped_unzoned,
                skipped_disallowed_zone_count=skipped_disallowed,
            ),
        )

    def _candidate(
        self,
        *,
        infrastructure_type: InfrastructureType,
        source_kind: InfrastructureCandidateSource,
        source_id: str,
        geometry: BaseGeometry,
        zone_class: ZoneClass,
        zone_id: str | None,
        block_id: str | None,
    ) -> InfrastructureSiteCandidate:
        _require_polygonal_geometry("candidate source geometry", geometry)
        anchor = geometry.representative_point()
        return InfrastructureSiteCandidate(
            candidate_id=(
                f"{infrastructure_type.code}:{source_kind.value}:{source_id}"
            ),
            infrastructure_type_code=infrastructure_type.code,
            source_kind=source_kind,
            source_id=source_id,
            zone_class=zone_class,
            working_srid=self.working_crs.srid,
            source_geometry=geometry,
            available_area_m2=float(geometry.area),
            anchor=anchor,
            zone_id=zone_id,
            block_id=block_id,
        )


def _require_polygonal_geometry(
    field_name: str,
    geometry: BaseGeometry,
) -> None:
    if not isinstance(geometry, (Polygon, MultiPolygon)):
        raise InfrastructureCandidateSiteError(
            f"{field_name} must be Polygon or MultiPolygon"
        )
    if geometry.is_empty or not geometry.is_valid or geometry.has_z:
        raise InfrastructureCandidateSiteError(
            f"{field_name} must be non-empty, valid and 2D"
        )
    area = float(geometry.area)
    if not math.isfinite(area) or area <= 0.0:
        raise InfrastructureCandidateSiteError(
            f"{field_name} must have positive finite area"
        )


def _require_id(field_name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise InfrastructureCandidateSiteError(
            f"{field_name} must be a non-empty string"
        )
    if "\n" in value or "\r" in value:
        raise InfrastructureCandidateSiteError(
            f"{field_name} must not contain line breaks"
        )


def _require_positive_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise InfrastructureCandidateSiteError(
            f"{field_name} must be a positive integer"
        )


def _require_non_negative_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise InfrastructureCandidateSiteError(
            f"{field_name} must be a non-negative integer"
        )


def _require_positive_finite(
    field_name: str,
    value: int | float,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InfrastructureCandidateSiteError(
            f"{field_name} must be a finite positive number"
        )
    number = float(value)
    if not math.isfinite(number) or number <= 0.0:
        raise InfrastructureCandidateSiteError(
            f"{field_name} must be a finite positive number"
        )
    return number
