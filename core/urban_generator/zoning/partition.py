from __future__ import annotations

import math
from dataclasses import dataclass

from shapely.geometry import MultiPoint, MultiPolygon, Point, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union, voronoi_diagram
from shapely.strtree import STRtree
from shapely.validation import make_valid

from core.urban_generator.domain.crs import require_working_crs
from core.urban_generator.zoning.seeds import ZoningSeed, ZoningSeedSet

_AREA_TOLERANCE_RATIO = 1e-9
_MIN_AREA_TOLERANCE_M2 = 1e-6


class ZoningPartitionError(ValueError):
    """Raised when base zoning partition geometry violates its spatial contract."""


@dataclass(frozen=True, slots=True)
class ZoningPartitionCell:
    """One developable-area-clipped Voronoi cell tied to its generating seed."""

    seed_index: int
    seed: ZoningSeed
    geometry: BaseGeometry
    area_m2: float
    validity_repaired: bool

    def __post_init__(self) -> None:
        if isinstance(self.seed_index, bool) or not isinstance(self.seed_index, int):
            raise ZoningPartitionError("seed_index must be an integer")
        if self.seed_index < 0:
            raise ZoningPartitionError("seed_index must be non-negative")
        if not isinstance(self.seed, ZoningSeed):
            raise ZoningPartitionError("seed must be a ZoningSeed")
        _require_polygonal_geometry("partition cell", self.geometry)
        if not self.geometry.is_valid:
            raise ZoningPartitionError("partition cell geometry must be valid")
        if not math.isfinite(self.area_m2) or self.area_m2 <= 0.0:
            raise ZoningPartitionError("partition cell area_m2 must be finite and positive")
        if not math.isclose(
            self.area_m2,
            float(self.geometry.area),
            rel_tol=1e-12,
            abs_tol=1e-9,
        ):
            raise ZoningPartitionError("partition cell area_m2 must match geometry area")
        if not isinstance(self.validity_repaired, bool):
            raise ZoningPartitionError("validity_repaired must be boolean")


@dataclass(frozen=True, slots=True)
class ZoningPartitionResult:
    """Deterministic base partition with coverage and validity diagnostics."""

    working_srid: int
    developable_area: BaseGeometry
    cells: tuple[ZoningPartitionCell, ...]
    developable_area_m2: float
    covered_area_m2: float
    uncovered_area_m2: float
    overlap_area_m2: float
    developable_validity_repaired: bool
    repaired_cell_count: int

    def __post_init__(self) -> None:
        require_working_crs(self.working_srid)
        _require_polygonal_geometry("developable_area", self.developable_area)
        if not self.developable_area.is_valid:
            raise ZoningPartitionError("developable_area must be valid in partition output")
        if not isinstance(self.cells, tuple) or not self.cells:
            raise ZoningPartitionError("cells must be a non-empty immutable tuple")
        if any(not isinstance(cell, ZoningPartitionCell) for cell in self.cells):
            raise ZoningPartitionError("cells must contain only ZoningPartitionCell values")
        if tuple(cell.seed_index for cell in self.cells) != tuple(range(len(self.cells))):
            raise ZoningPartitionError("partition cells must be ordered by contiguous seed_index")

        _require_non_negative_finite("developable_area_m2", self.developable_area_m2)
        _require_non_negative_finite("covered_area_m2", self.covered_area_m2)
        _require_non_negative_finite("uncovered_area_m2", self.uncovered_area_m2)
        _require_non_negative_finite("overlap_area_m2", self.overlap_area_m2)
        if self.developable_area_m2 <= 0.0:
            raise ZoningPartitionError("developable_area_m2 must be positive")
        if not isinstance(self.developable_validity_repaired, bool):
            raise ZoningPartitionError("developable_validity_repaired must be boolean")
        if (
            isinstance(self.repaired_cell_count, bool)
            or not isinstance(self.repaired_cell_count, int)
            or self.repaired_cell_count < 0
        ):
            raise ZoningPartitionError("repaired_cell_count must be a non-negative integer")
        if self.repaired_cell_count > len(self.cells):
            raise ZoningPartitionError("repaired_cell_count cannot exceed the number of cells")

    @property
    def coverage_ratio(self) -> float:
        return self.covered_area_m2 / self.developable_area_m2


class BaseZoningPartitioner:
    """Build deterministic Voronoi cells clipped to a polygonal developable area."""

    version = "1"

    def partition(
        self,
        *,
        seeds: ZoningSeedSet,
        developable_area: BaseGeometry,
        working_srid: int,
    ) -> ZoningPartitionResult:
        if not isinstance(seeds, ZoningSeedSet):
            raise ZoningPartitionError("seeds must be a ZoningSeedSet")
        if not seeds.seeds:
            raise ZoningPartitionError("at least one zoning seed is required")
        require_working_crs(working_srid)
        _require_polygonal_geometry("developable_area", developable_area, allow_invalid=True)

        repaired_developable, developable_repaired = _repair_polygonal(developable_area)
        if repaired_developable.area <= 0.0:
            raise ZoningPartitionError("developable_area must have positive polygonal area")

        seed_points = tuple(Point(seed.x_m, seed.y_m) for seed in seeds.seeds)
        coordinates = tuple((seed.x_m, seed.y_m) for seed in seeds.seeds)
        if len(coordinates) != len(set(coordinates)):
            raise ZoningPartitionError("zoning seed coordinates must be unique")
        for index, point in enumerate(seed_points):
            if not repaired_developable.covers(point):
                raise ZoningPartitionError(
                    f"zoning seed {index} must be covered by the developable area"
                )

        if len(seed_points) == 1:
            raw_cells = (repaired_developable,)
            raw_cell_indexes = (0,)
        else:
            raw_cells = _build_raw_voronoi_cells(seed_points, repaired_developable)
            raw_cell_indexes = _map_seed_points_to_cells(seed_points, raw_cells)

        cells: list[ZoningPartitionCell] = []
        for seed_index, raw_cell_index in enumerate(raw_cell_indexes):
            clipped = raw_cells[raw_cell_index].intersection(repaired_developable)
            repaired_cell, cell_repaired = _repair_polygonal(clipped)
            if repaired_cell.area <= 0.0:
                raise ZoningPartitionError(
                    f"partition cell for zoning seed {seed_index} has no polygonal area"
                )
            seed_point = seed_points[seed_index]
            if not repaired_cell.covers(seed_point):
                raise ZoningPartitionError(
                    f"partition cell for zoning seed {seed_index} does not cover its seed"
                )
            cells.append(
                ZoningPartitionCell(
                    seed_index=seed_index,
                    seed=seeds.seeds[seed_index],
                    geometry=repaired_cell,
                    area_m2=float(repaired_cell.area),
                    validity_repaired=cell_repaired,
                )
            )

        partition_union = unary_union(tuple(cell.geometry for cell in cells))
        covered_area = float(partition_union.area)
        developable_area_m2 = float(repaired_developable.area)
        uncovered_area = float(repaired_developable.difference(partition_union).area)
        outside_area = float(partition_union.difference(repaired_developable).area)
        summed_cell_area = sum(cell.area_m2 for cell in cells)
        overlap_area = max(0.0, summed_cell_area - covered_area)
        tolerance_m2 = max(
            _MIN_AREA_TOLERANCE_M2,
            developable_area_m2 * _AREA_TOLERANCE_RATIO,
        )
        if uncovered_area > tolerance_m2:
            raise ZoningPartitionError(
                "partition does not cover the developable area within tolerance: "
                f"{uncovered_area} m2 uncovered"
            )
        if outside_area > tolerance_m2:
            raise ZoningPartitionError(
                "partition extends outside the developable area within tolerance: "
                f"{outside_area} m2 outside"
            )
        if overlap_area > tolerance_m2:
            raise ZoningPartitionError(
                "partition cells overlap by positive area beyond tolerance: "
                f"{overlap_area} m2"
            )

        return ZoningPartitionResult(
            working_srid=working_srid,
            developable_area=repaired_developable,
            cells=tuple(cells),
            developable_area_m2=developable_area_m2,
            covered_area_m2=covered_area,
            uncovered_area_m2=uncovered_area,
            overlap_area_m2=overlap_area,
            developable_validity_repaired=developable_repaired,
            repaired_cell_count=sum(cell.validity_repaired for cell in cells),
        )


def _build_raw_voronoi_cells(
    seed_points: tuple[Point, ...],
    developable_area: BaseGeometry,
) -> tuple[BaseGeometry, ...]:
    diagram = voronoi_diagram(
        MultiPoint(seed_points),
        envelope=developable_area.envelope,
        tolerance=0.0,
        edges=False,
    )
    raw_cells = tuple(
        geometry
        for geometry in diagram.geoms
        if geometry.geom_type in {"Polygon", "MultiPolygon"} and not geometry.is_empty
    )
    if len(raw_cells) != len(seed_points):
        raise ZoningPartitionError(
            "Voronoi diagram must return exactly one polygonal cell per unique seed"
        )
    return raw_cells


def _map_seed_points_to_cells(
    seed_points: tuple[Point, ...],
    raw_cells: tuple[BaseGeometry, ...],
) -> tuple[int, ...]:
    tree = STRtree(raw_cells)
    mapped_indexes: list[int] = []
    used_indexes: set[int] = set()
    for seed_index, point in enumerate(seed_points):
        candidate_indexes = sorted(int(index) for index in tree.query(point))
        matches = [index for index in candidate_indexes if raw_cells[index].covers(point)]
        if len(matches) != 1:
            raise ZoningPartitionError(
                f"zoning seed {seed_index} must map to exactly one Voronoi cell"
            )
        raw_index = matches[0]
        if raw_index in used_indexes:
            raise ZoningPartitionError("two zoning seeds mapped to the same Voronoi cell")
        used_indexes.add(raw_index)
        mapped_indexes.append(raw_index)
    return tuple(mapped_indexes)


def _repair_polygonal(geometry: BaseGeometry) -> tuple[BaseGeometry, bool]:
    repaired = not geometry.is_valid
    candidate = make_valid(geometry) if repaired else geometry
    polygonal = _extract_polygonal(candidate)
    if not polygonal.is_valid:
        repaired = True
        polygonal = _extract_polygonal(make_valid(polygonal))
    if polygonal.is_empty:
        raise ZoningPartitionError("geometry repair produced no polygonal area")
    if not polygonal.is_valid:
        raise ZoningPartitionError("geometry remains invalid after make_valid repair")
    return polygonal, repaired


def _extract_polygonal(geometry: BaseGeometry) -> BaseGeometry:
    if geometry.geom_type in {"Polygon", "MultiPolygon"}:
        return geometry
    if geometry.geom_type != "GeometryCollection":
        raise ZoningPartitionError("geometry must contain polygonal area")

    polygons: list[Polygon] = []
    for part in geometry.geoms:
        if isinstance(part, Polygon):
            polygons.append(part)
        elif isinstance(part, MultiPolygon):
            polygons.extend(part.geoms)
    if not polygons:
        raise ZoningPartitionError("geometry collection contains no polygonal area")
    return unary_union(tuple(polygons))


def _require_polygonal_geometry(
    field_name: str,
    geometry: BaseGeometry,
    *,
    allow_invalid: bool = False,
) -> None:
    if not isinstance(geometry, BaseGeometry):
        raise ZoningPartitionError(f"{field_name} must be a Shapely geometry")
    if geometry.is_empty:
        raise ZoningPartitionError(f"{field_name} must not be empty")
    if geometry.geom_type not in {"Polygon", "MultiPolygon"}:
        raise ZoningPartitionError(f"{field_name} must be Polygon or MultiPolygon")
    if not allow_invalid and not geometry.is_valid:
        raise ZoningPartitionError(f"{field_name} must be valid")


def _require_non_negative_finite(field_name: str, value: float) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ZoningPartitionError(f"{field_name} must be a finite non-negative number")
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        raise ZoningPartitionError(f"{field_name} must be a finite non-negative number")
