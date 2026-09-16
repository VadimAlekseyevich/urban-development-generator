from __future__ import annotations

import math
import re
from dataclasses import dataclass

from shapely import normalize
from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union
from shapely.strtree import STRtree
from shapely.validation import make_valid

from core.urban_generator.blocks.polygonize import BlockPolygonizationResult
from core.urban_generator.domain.crs import WorkingCRS, require_working_crs

DEFAULT_MAX_CLIP_BLOCKS = 250_000
DEFAULT_MAX_HARD_CONSTRAINT_GEOMETRIES = 100_000
DEFAULT_MAX_CONSTRAINT_CANDIDATES_PER_BLOCK = 10_000
DEFAULT_MAX_CLIPPED_BLOCKS = 500_000

_CODE_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")


class BlockDevelopableClippingError(ValueError):
    """Raised when block clipping inputs violate the bounded metric contract."""


@dataclass(frozen=True, slots=True)
class BlockDevelopableArea:
    """Project and developable polygonal masks in one explicit metric CRS."""

    project_boundary: BaseGeometry
    developable_mask: BaseGeometry
    working_srid: int

    def __post_init__(self) -> None:
        require_working_crs(self.working_srid)
        _require_polygonal_geometry("project_boundary", self.project_boundary)
        _require_polygonal_geometry("developable_mask", self.developable_mask)


@dataclass(frozen=True, slots=True)
class BlockHardConstraintLayer:
    """One named immutable polygonal HARD exclusion layer for block clipping."""

    code: str
    geometries: tuple[BaseGeometry, ...]
    working_srid: int

    def __post_init__(self) -> None:
        _require_code(self.code)
        require_working_crs(self.working_srid)
        if not isinstance(self.geometries, tuple):
            raise BlockDevelopableClippingError("geometries must be an immutable tuple")
        for index, geometry in enumerate(self.geometries):
            _require_polygonal_geometry(f"geometries[{index}]", geometry)


@dataclass(frozen=True, slots=True)
class DevelopableBlockCandidate:
    """One polygonal block fragment remaining after T02 clipping."""

    block_id: str
    source_block_id: str
    source_fragment_index: int
    geometry: Polygon

    def __post_init__(self) -> None:
        _require_id("block_id", self.block_id)
        _require_id("source_block_id", self.source_block_id)
        if (
            isinstance(self.source_fragment_index, bool)
            or not isinstance(self.source_fragment_index, int)
            or self.source_fragment_index < 0
        ):
            raise BlockDevelopableClippingError(
                "source_fragment_index must be a non-negative integer"
            )
        if not isinstance(self.geometry, Polygon):
            raise BlockDevelopableClippingError("clipped block geometry must be a Polygon")
        _require_polygonal_geometry("clipped block geometry", self.geometry)


@dataclass(frozen=True, slots=True)
class BlockDevelopableClippingDiagnostics:
    """Bounded-work and topology diagnostics for one clipping pass."""

    input_block_count: int
    output_block_count: int
    mask_clipped_block_count: int
    mask_removed_block_count: int
    hard_constraint_hit_block_count: int
    hard_constraint_removed_block_count: int
    split_source_block_count: int
    hard_constraint_geometry_count: int
    constraint_candidate_count: int
    validity_repair_count: int


@dataclass(frozen=True, slots=True)
class BlockDevelopableClippingResult:
    """Developable block fragments ready for later S07 metrics and validation."""

    working_crs: WorkingCRS
    blocks: tuple[DevelopableBlockCandidate, ...]
    diagnostics: BlockDevelopableClippingDiagnostics


@dataclass(frozen=True, slots=True)
class _FragmentDraft:
    source_block_id: str
    source_fragment_index: int
    geometry: Polygon


class DevelopableBlockClipper:
    """Clip T01 block candidates to project/developable masks and HARD exclusions.

    The clipper performs only S07-T02 geometry work. It does not calculate block metrics,
    frontage/access, oversized splitting, sliver cleanup, zone association, parcel subdivision,
    persistence, API, or UI behavior.

    HARD exclusions are queried through one STRtree. Candidate work per source block is bounded,
    and no full block-by-constraint N×M scan is performed. All polygonal fragments are retained;
    area thresholds and sliver deletion/merge policy belong to S07-T06.
    """

    def __init__(
        self,
        *,
        working_srid: int,
        max_blocks: int = DEFAULT_MAX_CLIP_BLOCKS,
        max_hard_constraint_geometries: int = DEFAULT_MAX_HARD_CONSTRAINT_GEOMETRIES,
        max_constraint_candidates_per_block: int = DEFAULT_MAX_CONSTRAINT_CANDIDATES_PER_BLOCK,
        max_output_blocks: int = DEFAULT_MAX_CLIPPED_BLOCKS,
    ) -> None:
        self.working_crs = require_working_crs(working_srid)
        _require_positive_int("max_blocks", max_blocks)
        _require_positive_int(
            "max_hard_constraint_geometries",
            max_hard_constraint_geometries,
        )
        _require_positive_int(
            "max_constraint_candidates_per_block",
            max_constraint_candidates_per_block,
        )
        _require_positive_int("max_output_blocks", max_output_blocks)
        self.max_blocks = max_blocks
        self.max_hard_constraint_geometries = max_hard_constraint_geometries
        self.max_constraint_candidates_per_block = max_constraint_candidates_per_block
        self.max_output_blocks = max_output_blocks

    def clip(
        self,
        polygonization: BlockPolygonizationResult,
        *,
        area: BlockDevelopableArea,
        hard_constraints: tuple[BlockHardConstraintLayer, ...] = (),
    ) -> BlockDevelopableClippingResult:
        if not isinstance(polygonization, BlockPolygonizationResult):
            raise BlockDevelopableClippingError(
                "polygonization must be a BlockPolygonizationResult"
            )
        if polygonization.working_crs != self.working_crs:
            raise BlockDevelopableClippingError(
                "polygonization working CRS must match clipper working CRS"
            )
        if not isinstance(area, BlockDevelopableArea):
            raise BlockDevelopableClippingError("area must be a BlockDevelopableArea")
        if area.working_srid != self.working_crs.srid:
            raise BlockDevelopableClippingError(
                "developable area working_srid must match clipper working CRS"
            )
        if not isinstance(hard_constraints, tuple):
            raise BlockDevelopableClippingError(
                "hard_constraints must be an immutable tuple"
            )
        if len(polygonization.blocks) > self.max_blocks:
            raise BlockDevelopableClippingError(
                "block clipping input limit exceeded: "
                f"{len(polygonization.blocks)} > {self.max_blocks}"
            )

        seen_block_ids: set[str] = set()
        for block in polygonization.blocks:
            if block.block_id in seen_block_ids:
                raise BlockDevelopableClippingError(
                    f"duplicate source block_id: {block.block_id!r}"
                )
            seen_block_ids.add(block.block_id)

        constraint_geometries = self._flatten_hard_constraints(hard_constraints)
        constraint_tree = (
            STRtree(tuple(geometry for _code, geometry in constraint_geometries))
            if constraint_geometries
            else None
        )

        effective_developable_raw = area.project_boundary.intersection(area.developable_mask)
        effective_parts, mask_repaired = _polygon_parts(effective_developable_raw)
        effective_developable = _combine_polygon_parts(effective_parts)

        mask_clipped_block_count = 0
        mask_removed_block_count = 0
        hard_constraint_hit_block_count = 0
        hard_constraint_removed_block_count = 0
        split_source_block_count = 0
        constraint_candidate_count = 0
        validity_repair_count = int(mask_repaired)
        drafts: list[_FragmentDraft] = []

        ordered_blocks = sorted(polygonization.blocks, key=lambda block: block.block_id)
        for block in ordered_blocks:
            if effective_developable is None:
                mask_removed_block_count += 1
                continue

            masked_raw = block.geometry.intersection(effective_developable)
            masked_parts, masked_repaired = _polygon_parts(masked_raw)
            validity_repair_count += int(masked_repaired)
            masked_geometry = _combine_polygon_parts(masked_parts)
            if masked_geometry is None:
                mask_removed_block_count += 1
                continue
            if not masked_geometry.equals(block.geometry):
                mask_clipped_block_count += 1

            remaining_geometry = masked_geometry
            if constraint_tree is not None:
                candidate_indexes = tuple(
                    int(index) for index in constraint_tree.query(masked_geometry)
                )
                constraint_candidate_count += len(candidate_indexes)
                if len(candidate_indexes) > self.max_constraint_candidates_per_block:
                    raise BlockDevelopableClippingError(
                        "hard constraint candidate limit exceeded for block "
                        f"{block.block_id!r}: {len(candidate_indexes)} > "
                        f"{self.max_constraint_candidates_per_block}"
                    )

                ordered_indexes = sorted(
                    candidate_indexes,
                    key=lambda index: (
                        constraint_geometries[index][0],
                        constraint_geometries[index][1].wkb_hex,
                    ),
                )
                intersecting: list[BaseGeometry] = []
                for index in ordered_indexes:
                    constraint_geometry = constraint_geometries[index][1]
                    overlap = masked_geometry.intersection(constraint_geometry)
                    if not overlap.is_empty and float(overlap.area) > 0.0:
                        intersecting.append(constraint_geometry)

                if intersecting:
                    hard_constraint_hit_block_count += 1
                    exclusion = (
                        intersecting[0]
                        if len(intersecting) == 1
                        else unary_union(tuple(intersecting))
                    )
                    remaining_geometry = masked_geometry.difference(exclusion)

            final_parts, final_repaired = _polygon_parts(remaining_geometry)
            validity_repair_count += int(final_repaired)
            if not final_parts:
                hard_constraint_removed_block_count += 1
                continue
            if len(final_parts) > 1:
                split_source_block_count += 1

            ordered_parts = tuple(sorted(final_parts, key=lambda geometry: geometry.wkb_hex))
            for fragment_index, geometry in enumerate(ordered_parts):
                drafts.append(
                    _FragmentDraft(
                        source_block_id=block.block_id,
                        source_fragment_index=fragment_index,
                        geometry=geometry,
                    )
                )
                if len(drafts) > self.max_output_blocks:
                    raise BlockDevelopableClippingError(
                        "clipped block output limit exceeded: "
                        f"{len(drafts)} > {self.max_output_blocks}"
                    )

        blocks = tuple(
            DevelopableBlockCandidate(
                block_id=f"block:{index:08d}",
                source_block_id=draft.source_block_id,
                source_fragment_index=draft.source_fragment_index,
                geometry=draft.geometry,
            )
            for index, draft in enumerate(drafts)
        )
        diagnostics = BlockDevelopableClippingDiagnostics(
            input_block_count=len(polygonization.blocks),
            output_block_count=len(blocks),
            mask_clipped_block_count=mask_clipped_block_count,
            mask_removed_block_count=mask_removed_block_count,
            hard_constraint_hit_block_count=hard_constraint_hit_block_count,
            hard_constraint_removed_block_count=hard_constraint_removed_block_count,
            split_source_block_count=split_source_block_count,
            hard_constraint_geometry_count=len(constraint_geometries),
            constraint_candidate_count=constraint_candidate_count,
            validity_repair_count=validity_repair_count,
        )
        return BlockDevelopableClippingResult(
            working_crs=self.working_crs,
            blocks=blocks,
            diagnostics=diagnostics,
        )

    def _flatten_hard_constraints(
        self,
        hard_constraints: tuple[BlockHardConstraintLayer, ...],
    ) -> tuple[tuple[str, BaseGeometry], ...]:
        seen_codes: set[str] = set()
        flattened: list[tuple[str, BaseGeometry]] = []
        for index, layer in enumerate(hard_constraints):
            if not isinstance(layer, BlockHardConstraintLayer):
                raise BlockDevelopableClippingError(
                    f"hard_constraints[{index}] must be a BlockHardConstraintLayer"
                )
            if layer.working_srid != self.working_crs.srid:
                raise BlockDevelopableClippingError(
                    f"hard constraint layer {layer.code!r} working_srid must match clipper"
                )
            if layer.code in seen_codes:
                raise BlockDevelopableClippingError(
                    f"duplicate hard constraint layer code: {layer.code!r}"
                )
            seen_codes.add(layer.code)
            flattened.extend((layer.code, geometry) for geometry in layer.geometries)

        if len(flattened) > self.max_hard_constraint_geometries:
            raise BlockDevelopableClippingError(
                "hard constraint geometry limit exceeded: "
                f"{len(flattened)} > {self.max_hard_constraint_geometries}"
            )
        flattened.sort(key=lambda item: (item[0], item[1].wkb_hex))
        return tuple(flattened)


def _polygon_parts(geometry: BaseGeometry) -> tuple[tuple[Polygon, ...], bool]:
    if geometry.is_empty:
        return (), False

    repaired = not geometry.is_valid
    candidate = make_valid(geometry) if repaired else geometry
    parts = _extract_polygon_parts(candidate)
    normalized_parts: list[Polygon] = []
    for part in parts:
        normalized = normalize(part)
        if not isinstance(normalized, Polygon):
            raise BlockDevelopableClippingError(
                "normalized clipping output must remain polygonal"
            )
        if not normalized.is_valid:
            repaired = True
            valid = make_valid(normalized)
            nested = _extract_polygon_parts(valid)
            normalized_parts.extend(_normalize_valid_parts(nested))
            continue
        if normalized.is_empty or normalized.has_z:
            raise BlockDevelopableClippingError(
                "clipping output polygons must be non-empty and 2D"
            )
        area = float(normalized.area)
        if not math.isfinite(area) or area <= 0.0:
            raise BlockDevelopableClippingError(
                "clipping output polygons must have positive finite area"
            )
        normalized_parts.append(normalized)

    normalized_parts.sort(key=lambda item: item.wkb_hex)
    return tuple(normalized_parts), repaired


def _normalize_valid_parts(parts: tuple[Polygon, ...]) -> tuple[Polygon, ...]:
    normalized_parts: list[Polygon] = []
    for part in parts:
        normalized = normalize(part)
        if not isinstance(normalized, Polygon) or not normalized.is_valid:
            raise BlockDevelopableClippingError(
                "geometry remains invalid after make_valid repair"
            )
        if normalized.is_empty or normalized.has_z:
            raise BlockDevelopableClippingError(
                "repaired clipping output polygons must be non-empty and 2D"
            )
        area = float(normalized.area)
        if not math.isfinite(area) or area <= 0.0:
            raise BlockDevelopableClippingError(
                "repaired clipping output polygons must have positive finite area"
            )
        normalized_parts.append(normalized)
    return tuple(normalized_parts)


def _extract_polygon_parts(geometry: BaseGeometry) -> tuple[Polygon, ...]:
    if geometry.is_empty:
        return ()
    if isinstance(geometry, Polygon):
        return (geometry,)
    if isinstance(geometry, MultiPolygon):
        return tuple(geometry.geoms)
    if geometry.geom_type == "GeometryCollection":
        parts: list[Polygon] = []
        for child in geometry.geoms:
            parts.extend(_extract_polygon_parts(child))
        return tuple(parts)
    return ()


def _combine_polygon_parts(parts: tuple[Polygon, ...]) -> BaseGeometry | None:
    if not parts:
        return None
    if len(parts) == 1:
        return parts[0]
    combined = MultiPolygon(parts)
    if combined.is_valid:
        return combined
    repaired = make_valid(combined)
    repaired_parts = _extract_polygon_parts(repaired)
    if not repaired_parts:
        return None
    if len(repaired_parts) == 1:
        return repaired_parts[0]
    return MultiPolygon(repaired_parts)


def _require_polygonal_geometry(field_name: str, geometry: BaseGeometry) -> None:
    if not isinstance(geometry, BaseGeometry):
        raise BlockDevelopableClippingError(f"{field_name} must be a Shapely geometry")
    if geometry.is_empty:
        raise BlockDevelopableClippingError(f"{field_name} must not be empty")
    if geometry.geom_type not in {"Polygon", "MultiPolygon"}:
        raise BlockDevelopableClippingError(
            f"{field_name} must be Polygon or MultiPolygon"
        )
    if not geometry.is_valid:
        raise BlockDevelopableClippingError(f"{field_name} must be valid")
    if geometry.has_z:
        raise BlockDevelopableClippingError(f"{field_name} must be 2D")
    bounds = geometry.bounds
    if len(bounds) != 4 or any(not math.isfinite(float(value)) for value in bounds):
        raise BlockDevelopableClippingError(f"{field_name} must have finite bounds")
    area = float(geometry.area)
    if not math.isfinite(area) or area <= 0.0:
        raise BlockDevelopableClippingError(
            f"{field_name} must have positive finite area"
        )


def _require_code(code: str) -> None:
    if not isinstance(code, str) or _CODE_RE.fullmatch(code) is None:
        raise BlockDevelopableClippingError(
            f"invalid hard constraint layer code: {code!r}"
        )


def _require_id(field_name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise BlockDevelopableClippingError(f"{field_name} must be a non-empty string")
    if "\n" in value or "\r" in value:
        raise BlockDevelopableClippingError(f"{field_name} must not contain line breaks")


def _require_positive_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise BlockDevelopableClippingError(f"{field_name} must be a positive integer")
