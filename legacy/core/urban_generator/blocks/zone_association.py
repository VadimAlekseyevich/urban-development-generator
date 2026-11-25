from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.strtree import STRtree

from core.urban_generator.blocks.sliver_cleanup import (
    CleanedBlockCandidate,
    SliverCleanupResult,
)
from core.urban_generator.domain.crs import WorkingCRS, require_working_crs
from core.urban_generator.zoning.config import ZoneClass

DEFAULT_MAX_ZONE_ASSOCIATION_BLOCKS = 1_000_000
DEFAULT_MAX_ZONE_ASSOCIATION_ZONES = 250_000
DEFAULT_MAX_ZONE_CANDIDATES_PER_BLOCK = 10_000

_AREA_REL_TOLERANCE = 1e-9
_AREA_ABS_TOLERANCE_M2 = 1e-6


class BlockZoneAssociationError(ValueError):
    """Raised when block-zone association violates the bounded metric-CRS contract."""


class BlockZoneAssociationStatus(StrEnum):
    """Whether one cleaned block can be related to one zone without guessing."""

    ASSOCIATED = "ASSOCIATED"
    NO_OVERLAP = "NO_OVERLAP"
    PARTIAL_OVERLAP = "PARTIAL_OVERLAP"
    AMBIGUOUS_FULL_COVERAGE = "AMBIGUOUS_FULL_COVERAGE"


@dataclass(frozen=True, slots=True)
class BlockZoneReference:
    """Persistence-ready zone identity plus its polygonal geometry in the working CRS.

    ``zone_id`` is intentionally a string rather than a database-specific UUID. A caller may use
    a persisted ``GeneratedZone.id`` string or a canonical fixed-zone feature identifier without
    introducing SQLAlchemy/storage dependencies into core.
    """

    zone_id: str
    zone_class: ZoneClass
    geometry: BaseGeometry
    working_srid: int

    def __post_init__(self) -> None:
        _require_id("zone_id", self.zone_id)
        if not isinstance(self.zone_class, ZoneClass):
            raise BlockZoneAssociationError("zone_class must be a ZoneClass")
        require_working_crs(self.working_srid)
        _require_polygonal_geometry("zone geometry", self.geometry)


@dataclass(frozen=True, slots=True)
class BlockZoneAssociation:
    """One explicit block→zone decision with enough diagnostics to avoid a downstream re-join."""

    status: BlockZoneAssociationStatus
    zone_id: str | None
    zone_class: ZoneClass | None
    positive_overlap_zone_ids: tuple[str, ...]
    maximum_overlap_ratio: float

    def __post_init__(self) -> None:
        if not isinstance(self.status, BlockZoneAssociationStatus):
            raise BlockZoneAssociationError(
                "status must be a BlockZoneAssociationStatus"
            )
        _require_sorted_unique_ids(
            "positive_overlap_zone_ids",
            self.positive_overlap_zone_ids,
        )
        _require_finite_range(
            "maximum_overlap_ratio",
            self.maximum_overlap_ratio,
            minimum=0.0,
            maximum=1.0,
        )

        if self.status is BlockZoneAssociationStatus.ASSOCIATED:
            if self.zone_id is None or self.zone_class is None:
                raise BlockZoneAssociationError(
                    "ASSOCIATED status requires zone_id and zone_class"
                )
            _require_id("zone_id", self.zone_id)
            if not isinstance(self.zone_class, ZoneClass):
                raise BlockZoneAssociationError("zone_class must be a ZoneClass")
            if self.positive_overlap_zone_ids != (self.zone_id,):
                raise BlockZoneAssociationError(
                    "associated zone must be the only positive-overlap zone"
                )
            if not math.isclose(
                self.maximum_overlap_ratio,
                1.0,
                rel_tol=0.0,
                abs_tol=1e-12,
            ):
                raise BlockZoneAssociationError(
                    "associated block must have full zone coverage"
                )
        else:
            if self.zone_id is not None or self.zone_class is not None:
                raise BlockZoneAssociationError(
                    "unassociated status must not carry zone_id or zone_class"
                )
            if self.status is BlockZoneAssociationStatus.NO_OVERLAP:
                if self.positive_overlap_zone_ids:
                    raise BlockZoneAssociationError(
                        "NO_OVERLAP must not contain positive-overlap zones"
                    )
                if self.maximum_overlap_ratio != 0.0:
                    raise BlockZoneAssociationError(
                        "NO_OVERLAP maximum_overlap_ratio must be zero"
                    )
            elif not self.positive_overlap_zone_ids:
                raise BlockZoneAssociationError(
                    "partial/ambiguous association must list positive-overlap zones"
                )


@dataclass(frozen=True, slots=True)
class ZoneAssociatedBlock:
    """One immutable T06 block paired with its explicit T07 zone relation."""

    cleaned_block: CleanedBlockCandidate
    association: BlockZoneAssociation

    def __post_init__(self) -> None:
        if not isinstance(self.cleaned_block, CleanedBlockCandidate):
            raise BlockZoneAssociationError(
                "cleaned_block must be a CleanedBlockCandidate"
            )
        if not isinstance(self.association, BlockZoneAssociation):
            raise BlockZoneAssociationError(
                "association must be a BlockZoneAssociation"
            )


@dataclass(frozen=True, slots=True)
class BlockZoneAssociationDiagnostics:
    """Aggregate association outcomes and bounded spatial-index work."""

    block_count: int
    zone_count: int
    associated_block_count: int
    no_overlap_block_count: int
    partial_overlap_block_count: int
    ambiguous_block_count: int
    spatial_candidate_pair_count: int
    positive_overlap_pair_count: int

    def __post_init__(self) -> None:
        for field_name in (
            "block_count",
            "zone_count",
            "associated_block_count",
            "no_overlap_block_count",
            "partial_overlap_block_count",
            "ambiguous_block_count",
            "spatial_candidate_pair_count",
            "positive_overlap_pair_count",
        ):
            _require_non_negative_int(field_name, getattr(self, field_name))
        if (
            self.associated_block_count
            + self.no_overlap_block_count
            + self.partial_overlap_block_count
            + self.ambiguous_block_count
            != self.block_count
        ):
            raise BlockZoneAssociationError(
                "association outcome counts must sum to block_count"
            )
        if self.positive_overlap_pair_count > self.spatial_candidate_pair_count:
            raise BlockZoneAssociationError(
                "positive overlap pairs cannot exceed spatial candidate pairs"
            )


@dataclass(frozen=True, slots=True)
class BlockZoneAssociationResult:
    """Deterministic T07 output carrying explicit zone IDs for downstream stages."""

    working_crs: WorkingCRS
    blocks: tuple[ZoneAssociatedBlock, ...]
    diagnostics: BlockZoneAssociationDiagnostics

    def __post_init__(self) -> None:
        if not isinstance(self.working_crs, WorkingCRS):
            raise BlockZoneAssociationError("working_crs must be a WorkingCRS")
        if not isinstance(self.blocks, tuple):
            raise BlockZoneAssociationError("blocks must be an immutable tuple")
        if any(not isinstance(block, ZoneAssociatedBlock) for block in self.blocks):
            raise BlockZoneAssociationError(
                "blocks must contain only ZoneAssociatedBlock values"
            )
        if not isinstance(self.diagnostics, BlockZoneAssociationDiagnostics):
            raise BlockZoneAssociationError(
                "diagnostics must be a BlockZoneAssociationDiagnostics"
            )
        if self.diagnostics.block_count != len(self.blocks):
            raise BlockZoneAssociationError(
                "diagnostics block_count must match result blocks"
            )


class BlockZoneAssociator:
    """Associate cleaned blocks to exactly one fully covering zone using one STRtree.

    T07 intentionally refuses to guess when a block crosses a zone boundary. A relation is emitted
    only when exactly one zone has positive overlap and covers the complete block area within the
    metric tolerance. Partial overlaps and multiple full-cover zones remain explicit unassociated
    statuses, so downstream stages can consume the relation without silently repeating a spatial
    join or inheriting an arbitrary dominant-zone decision.
    """

    def __init__(
        self,
        *,
        working_srid: int,
        max_blocks: int = DEFAULT_MAX_ZONE_ASSOCIATION_BLOCKS,
        max_zones: int = DEFAULT_MAX_ZONE_ASSOCIATION_ZONES,
        max_candidates_per_block: int = DEFAULT_MAX_ZONE_CANDIDATES_PER_BLOCK,
    ) -> None:
        self.working_crs = require_working_crs(working_srid)
        _require_positive_int("max_blocks", max_blocks)
        _require_positive_int("max_zones", max_zones)
        _require_positive_int("max_candidates_per_block", max_candidates_per_block)
        self.max_blocks = max_blocks
        self.max_zones = max_zones
        self.max_candidates_per_block = max_candidates_per_block

    def associate(
        self,
        cleanup: SliverCleanupResult,
        *,
        zones: tuple[BlockZoneReference, ...],
    ) -> BlockZoneAssociationResult:
        if not isinstance(cleanup, SliverCleanupResult):
            raise BlockZoneAssociationError("cleanup must be a SliverCleanupResult")
        if cleanup.working_crs != self.working_crs:
            raise BlockZoneAssociationError(
                "cleanup working CRS must match associator working CRS"
            )
        if not isinstance(zones, tuple):
            raise BlockZoneAssociationError("zones must be an immutable tuple")
        if len(cleanup.blocks) > self.max_blocks:
            raise BlockZoneAssociationError(
                f"zone association block limit exceeded: {len(cleanup.blocks)} > "
                f"{self.max_blocks}"
            )
        if len(zones) > self.max_zones:
            raise BlockZoneAssociationError(
                f"zone association zone limit exceeded: {len(zones)} > {self.max_zones}"
            )

        ordered_zones = tuple(sorted(zones, key=lambda zone: zone.zone_id))
        self._validate_zones(ordered_zones)
        zone_geometries = tuple(zone.geometry for zone in ordered_zones)
        tree = STRtree(zone_geometries) if zone_geometries else None

        associated_blocks: list[ZoneAssociatedBlock] = []
        spatial_candidate_pair_count = 0
        positive_overlap_pair_count = 0

        ordered_blocks = tuple(
            sorted(cleanup.blocks, key=lambda block: block.block_id)
        )
        seen_block_ids: set[str] = set()
        for cleaned_block in ordered_blocks:
            if cleaned_block.block_id in seen_block_ids:
                raise BlockZoneAssociationError(
                    f"duplicate cleaned block_id: {cleaned_block.block_id!r}"
                )
            seen_block_ids.add(cleaned_block.block_id)

            if tree is None:
                association = _no_overlap_association()
            else:
                candidate_indexes = tuple(
                    sorted(int(index) for index in tree.query(cleaned_block.geometry))
                )
                spatial_candidate_pair_count += len(candidate_indexes)
                if len(candidate_indexes) > self.max_candidates_per_block:
                    raise BlockZoneAssociationError(
                        "zone candidate limit exceeded for block "
                        f"{cleaned_block.block_id!r}: {len(candidate_indexes)} > "
                        f"{self.max_candidates_per_block}"
                    )

                overlaps: list[tuple[int, float, float]] = []
                block_area_m2 = float(cleaned_block.geometry.area)
                _require_positive_finite("block area", block_area_m2)
                for index in candidate_indexes:
                    overlap_area_m2 = float(
                        cleaned_block.geometry.intersection(zone_geometries[index]).area
                    )
                    _require_non_negative_finite("zone overlap area", overlap_area_m2)
                    tolerance_m2 = max(
                        _AREA_ABS_TOLERANCE_M2,
                        block_area_m2 * _AREA_REL_TOLERANCE,
                    )
                    if overlap_area_m2 <= tolerance_m2:
                        continue
                    positive_overlap_pair_count += 1
                    overlap_ratio = min(1.0, overlap_area_m2 / block_area_m2)
                    overlaps.append((index, overlap_area_m2, overlap_ratio))

                association = _classify_association(
                    overlaps=tuple(overlaps),
                    zones=ordered_zones,
                    block_area_m2=block_area_m2,
                )

            associated_blocks.append(
                ZoneAssociatedBlock(
                    cleaned_block=cleaned_block,
                    association=association,
                )
            )

        blocks = tuple(associated_blocks)
        diagnostics = BlockZoneAssociationDiagnostics(
            block_count=len(blocks),
            zone_count=len(ordered_zones),
            associated_block_count=sum(
                block.association.status is BlockZoneAssociationStatus.ASSOCIATED
                for block in blocks
            ),
            no_overlap_block_count=sum(
                block.association.status is BlockZoneAssociationStatus.NO_OVERLAP
                for block in blocks
            ),
            partial_overlap_block_count=sum(
                block.association.status is BlockZoneAssociationStatus.PARTIAL_OVERLAP
                for block in blocks
            ),
            ambiguous_block_count=sum(
                block.association.status
                is BlockZoneAssociationStatus.AMBIGUOUS_FULL_COVERAGE
                for block in blocks
            ),
            spatial_candidate_pair_count=spatial_candidate_pair_count,
            positive_overlap_pair_count=positive_overlap_pair_count,
        )
        return BlockZoneAssociationResult(
            working_crs=self.working_crs,
            blocks=blocks,
            diagnostics=diagnostics,
        )

    def _validate_zones(self, zones: tuple[BlockZoneReference, ...]) -> None:
        seen_ids: set[str] = set()
        for index, zone in enumerate(zones):
            if not isinstance(zone, BlockZoneReference):
                raise BlockZoneAssociationError(
                    f"zones[{index}] must be a BlockZoneReference"
                )
            if zone.working_srid != self.working_crs.srid:
                raise BlockZoneAssociationError(
                    f"zone {zone.zone_id!r} working_srid must match associator working CRS"
                )
            if zone.zone_id in seen_ids:
                raise BlockZoneAssociationError(
                    f"duplicate zone_id: {zone.zone_id!r}"
                )
            seen_ids.add(zone.zone_id)


def _classify_association(
    *,
    overlaps: tuple[tuple[int, float, float], ...],
    zones: tuple[BlockZoneReference, ...],
    block_area_m2: float,
) -> BlockZoneAssociation:
    if not overlaps:
        return _no_overlap_association()

    ordered_overlaps = tuple(
        sorted(overlaps, key=lambda item: zones[item[0]].zone_id)
    )
    positive_ids = tuple(zones[index].zone_id for index, _area, _ratio in ordered_overlaps)
    maximum_overlap_ratio = max(ratio for _index, _area, ratio in ordered_overlaps)
    tolerance_m2 = max(
        _AREA_ABS_TOLERANCE_M2,
        block_area_m2 * _AREA_REL_TOLERANCE,
    )
    full_cover = tuple(
        (index, overlap_area_m2)
        for index, overlap_area_m2, _ratio in ordered_overlaps
        if math.isclose(
            overlap_area_m2,
            block_area_m2,
            rel_tol=_AREA_REL_TOLERANCE,
            abs_tol=tolerance_m2,
        )
    )

    if len(full_cover) == 1 and len(ordered_overlaps) == 1:
        zone = zones[full_cover[0][0]]
        return BlockZoneAssociation(
            status=BlockZoneAssociationStatus.ASSOCIATED,
            zone_id=zone.zone_id,
            zone_class=zone.zone_class,
            positive_overlap_zone_ids=(zone.zone_id,),
            maximum_overlap_ratio=1.0,
        )
    if full_cover:
        return BlockZoneAssociation(
            status=BlockZoneAssociationStatus.AMBIGUOUS_FULL_COVERAGE,
            zone_id=None,
            zone_class=None,
            positive_overlap_zone_ids=positive_ids,
            maximum_overlap_ratio=maximum_overlap_ratio,
        )
    return BlockZoneAssociation(
        status=BlockZoneAssociationStatus.PARTIAL_OVERLAP,
        zone_id=None,
        zone_class=None,
        positive_overlap_zone_ids=positive_ids,
        maximum_overlap_ratio=maximum_overlap_ratio,
    )


def _no_overlap_association() -> BlockZoneAssociation:
    return BlockZoneAssociation(
        status=BlockZoneAssociationStatus.NO_OVERLAP,
        zone_id=None,
        zone_class=None,
        positive_overlap_zone_ids=(),
        maximum_overlap_ratio=0.0,
    )


def _require_polygonal_geometry(field_name: str, geometry: BaseGeometry) -> None:
    if not isinstance(geometry, (Polygon, MultiPolygon)):
        raise BlockZoneAssociationError(
            f"{field_name} must be Polygon or MultiPolygon"
        )
    if geometry.is_empty or not geometry.is_valid or geometry.has_z:
        raise BlockZoneAssociationError(
            f"{field_name} must be non-empty, valid and 2D"
        )
    _require_positive_finite(f"{field_name} area", float(geometry.area))


def _require_sorted_unique_ids(field_name: str, values: tuple[str, ...]) -> None:
    if not isinstance(values, tuple):
        raise BlockZoneAssociationError(f"{field_name} must be an immutable tuple")
    for value in values:
        _require_id(field_name, value)
    if values != tuple(sorted(values)) or len(values) != len(set(values)):
        raise BlockZoneAssociationError(f"{field_name} must be sorted and unique")


def _require_id(field_name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise BlockZoneAssociationError(f"{field_name} must be a non-empty string")
    if "\n" in value or "\r" in value:
        raise BlockZoneAssociationError(f"{field_name} must not contain line breaks")


def _require_positive_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise BlockZoneAssociationError(f"{field_name} must be a positive integer")


def _require_non_negative_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise BlockZoneAssociationError(f"{field_name} must be a non-negative integer")


def _require_positive_finite(field_name: str, value: float) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BlockZoneAssociationError(f"{field_name} must be a positive finite number")
    number = float(value)
    if not math.isfinite(number) or number <= 0.0:
        raise BlockZoneAssociationError(f"{field_name} must be a positive finite number")


def _require_non_negative_finite(field_name: str, value: float) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BlockZoneAssociationError(
            f"{field_name} must be a finite non-negative number"
        )
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        raise BlockZoneAssociationError(
            f"{field_name} must be a finite non-negative number"
        )


def _require_finite_range(
    field_name: str,
    value: float,
    *,
    minimum: float,
    maximum: float,
) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BlockZoneAssociationError(f"{field_name} must be a finite number")
    number = float(value)
    if not math.isfinite(number) or number < minimum or number > maximum:
        raise BlockZoneAssociationError(
            f"{field_name} must be between {minimum} and {maximum} inclusive"
        )
