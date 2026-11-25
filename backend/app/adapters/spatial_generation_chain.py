"""Actual canonical stage bindings through demography, not a complete worker runtime."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import asdict, replace

import numpy as np
from rasterio.features import shapes
from rasterio.transform import from_bounds
from shapely.geometry import GeometryCollection, MultiPolygon, Polygon, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union
from sqlalchemy.orm import Session

from backend.app.adapters.dem_source_reader import SqlAlchemyDEMSourceReader
from backend.app.adapters.early_generation_inputs import (
    EarlyGenerationInputResolver,
    RunScopedDEMSlopeFactor,
)
from backend.app.adapters.fixed_road_source_reader import SqlAlchemyFixedRoadSourceReader
from backend.app.adapters.hard_exclusion_source_reader import SqlAlchemyHardExclusionSourceReader
from backend.app.adapters.urban_polygon_sources import SqlAlchemyUrbanPolygonSourceReader
from backend.app.application.generation import GenerationExecutionError, StageInvocation
from backend.app.db.session import SessionLocal
from core.urban_generator.blocks import BlockDevelopableArea
from core.urban_generator.domain import (
    ConstraintEngine,
    PipelineContext,
    RunContext,
    TerritorySnapshot,
)
from core.urban_generator.stages import (
    BlocksAndParcelsStage,
    BlocksAndParcelsStageConfig,
    BlocksAndParcelsStageInput,
    BlocksAndParcelsStageOutput,
    BuildingStage,
    BuildingStageConfig,
    BuildingStageInput,
    BuildingStageOutput,
    ConstraintMaskStage,
    ConstraintMaskStageInput,
    DemographyStage,
    DemographyStageConfig,
    DemographyStageInput,
    PrepareSnapshotStage,
    RoadStage,
    RoadStageConfig,
    RoadStageInput,
    RoadStageOutput,
    StageRegistry,
    SuitabilityStage,
    SuitabilityStageConfig,
    ZoningStage,
    ZoningStageConfig,
    ZoningStageInput,
    ZoningStageOutput,
)
from core.urban_generator.stages.registry import StageAny
from core.urban_generator.stages.suitability import PreparedSuitabilityStageConfig
from core.urban_generator.suitability import (
    HardExclusionMask,
    LanduseClassWeights,
    LanduseFactor,
    RoadProximityFactor,
    RoadProximityIndex,
    SuitabilityFactor,
    SuitabilityFactorResult,
    SuitabilityGridSpec,
    WeightedSuitabilityResult,
)

_MAX_MASK_POLYGONS = 100_000
_MAX_MASK_COORDINATES = 2_000_000


class RunScopedRoadProximityFactor:
    code = "road_proximity"
    version = "v1"

    def __init__(self, reader: SqlAlchemyFixedRoadSourceReader) -> None:
        self._reader = reader

    def evaluate(
        self, *, grid: SuitabilityGridSpec, snapshot: TerritorySnapshot, context: RunContext,
    ) -> SuitabilityFactorResult:
        roads = self._reader.read(snapshot=snapshot)
        index = RoadProximityIndex(
            roads=tuple(item.road.geometry for item in roads), working_srid=grid.working_srid,
        )
        return RoadProximityFactor(index=index).evaluate(
            grid=grid, snapshot=snapshot, context=context,
        )


class RunScopedLanduseFactor:
    code = "landuse"
    version = "v1"

    def __init__(
        self, reader: SqlAlchemyUrbanPolygonSourceReader, weights: LanduseClassWeights,
    ) -> None:
        self._reader = reader
        self._weights = weights

    def evaluate(
        self, *, grid: SuitabilityGridSpec, snapshot: TerritorySnapshot, context: RunContext,
    ) -> SuitabilityFactorResult:
        source = self._reader.read_landuse(snapshot=snapshot, grid=grid)
        return LanduseFactor(source=source, weights=self._weights).evaluate(
            grid=grid, snapshot=snapshot, context=context,
        )


def _require[T](outputs: Mapping[str, object], name: str, expected: type[T]) -> T:
    value = outputs.get(name)
    if not isinstance(value, expected):
        raise GenerationExecutionError(f"missing or invalid stage output: {name}")
    return value


def _polygonal(value: BaseGeometry) -> tuple[Polygon, ...]:
    if isinstance(value, Polygon):
        return (value,) if value.area > 0 else ()
    if isinstance(value, (MultiPolygon, GeometryCollection)):
        return tuple(
            part for child in value.geoms for part in _polygonal(child)
        )
    return ()


def build_developable_area(
    *, prepared: ConstraintMaskStageInput, suitability: WeightedSuitabilityResult,
    minimum_score: float,
) -> tuple[BaseGeometry, WeightedSuitabilityResult]:
    """Polygonize eligible cells, clip to exact boundary, exclude hard polygons.

    Retain the original suitability result for reporting. Zoning receives a
    derived mask so it cannot select below-threshold or invalid cells.
    """
    if prepared.grid != suitability.grid:
        raise GenerationExecutionError("prepared and suitability grids differ")
    grid = suitability.grid
    eligible = suitability.valid_mask & (suitability.scores >= minimum_score)
    if not np.any(eligible):
        raise GenerationExecutionError("no developable cells meet suitability threshold")
    polygons: list[BaseGeometry] = []
    coordinates = 0
    for geom, _ in shapes(
        eligible.astype(np.uint8), mask=eligible,
        transform=from_bounds(*grid.bounds, grid.width, grid.height),
    ):
        polygon = shape(geom)
        polygons.append(polygon)
        if isinstance(polygon, Polygon):
            coordinates += len(polygon.exterior.coords)
            coordinates += sum(len(ring.coords) for ring in polygon.interiors)
        if len(polygons) > _MAX_MASK_POLYGONS or coordinates > _MAX_MASK_COORDINATES:
            raise GenerationExecutionError("developable raster polygonization exceeds budget")
    area = unary_union(polygons).intersection(prepared.boundary.geometry)
    hard = [
        geom for layer in prepared.geometry_layers for geom in layer.geometries
        if geom.geom_type in {"Polygon", "MultiPolygon"}
    ]
    if hard:
        area = area.difference(unary_union(hard))
    parts = _polygonal(area)
    area = unary_union(parts)
    if area.is_empty or not area.is_valid or area.area <= 0:
        raise GenerationExecutionError("no valid polygonal developable area")
    filtered = replace(
        suitability, valid_mask=eligible, scores=np.where(eligible, suitability.scores, 0.0),
    )
    return area, filtered


def _json_default(value: object) -> object:
    if isinstance(value, frozenset):
        return sorted(value, key=str)
    raise TypeError(f"unsupported config value: {type(value).__name__}")


class SpatialGenerationInputResolver(EarlyGenerationInputResolver):
    """Bind existing stages to selected actual sources and completed typed outputs."""

    def __init__(
        self, *, session_factory: Callable[[], Session] = SessionLocal,
    ) -> None:
        super().__init__(SqlAlchemyHardExclusionSourceReader(session_factory=session_factory))
        self._roads = SqlAlchemyFixedRoadSourceReader(session_factory=session_factory)
        self._polygons = SqlAlchemyUrbanPolygonSourceReader(session_factory=session_factory)

    def resolve(
        self, *, stage: StageAny, context: PipelineContext, config: object,
        outputs: Mapping[str, object],
    ) -> StageInvocation:
        if stage.name in {
            PrepareSnapshotStage.name, ConstraintMaskStage.name, SuitabilityStage.name,
        }:
            return super().resolve(stage=stage, context=context, config=config, outputs=outputs)
        parts: tuple[str | bytes, ...] = ()
        value: object
        if stage.name == ZoningStage.name and isinstance(config, ZoningStageConfig):
            if context.snapshot.fixed_zones:
                raise GenerationExecutionError("fixed-zone geometry hydration not implemented")
            prepared = _require(outputs, PrepareSnapshotStage.name, ConstraintMaskStageInput)
            suitability = _require(outputs, SuitabilityStage.name, WeightedSuitabilityResult)
            settings = context.require_config(SuitabilityStage.name, SuitabilityStageConfig)
            area, filtered = build_developable_area(
                prepared=prepared, suitability=suitability,
                minimum_score=settings.suitability.thresholds.minimum_score,
            )
            value = ZoningStageInput(suitability=filtered, developable_area=area)
            parts = (area.wkb, settings.suitability.fingerprint)
        elif stage.name == RoadStage.name and isinstance(config, RoadStageConfig):
            zoning = _require(outputs, ZoningStage.name, ZoningStageOutput)
            suitability = _require(outputs, SuitabilityStage.name, WeightedSuitabilityResult)
            mask = _require(outputs, ConstraintMaskStage.name, HardExclusionMask)
            prepared = _require(outputs, PrepareSnapshotStage.name, ConstraintMaskStageInput)
            roads = self._roads.read(snapshot=context.snapshot)
            footprints = self._polygons.read_buildings(snapshot=context.snapshot)
            forbidden = (
                *(geom for layer in prepared.geometry_layers for geom in layer.geometries),
                *(item.geometry for item in footprints),
            )
            value = RoadStageInput(
                zoning=zoning, suitability=suitability, hard_mask=mask,
                fixed_roads=roads, forbidden_geometries=forbidden,
            )
            parts = (
                *(ref.source_ref for ref in (*context.snapshot.roads, *context.snapshot.buildings)),
                *(geom.wkb for geom in forbidden),
                *(item.road.geometry.wkb for item in roads),
            )
        elif stage.name == BlocksAndParcelsStage.name and isinstance(
            config, BlocksAndParcelsStageConfig,
        ):
            zoning = _require(outputs, ZoningStage.name, ZoningStageOutput)
            roads_output = _require(outputs, RoadStage.name, RoadStageOutput)
            prepared = _require(outputs, PrepareSnapshotStage.name, ConstraintMaskStageInput)
            area = zoning.partition.developable_area
            value = BlocksAndParcelsStageInput(
                road_graph=roads_output.graph,
                developable_area=BlockDevelopableArea(
                    project_boundary=prepared.boundary.geometry,
                    developable_mask=area, working_srid=context.run.working_srid,
                ),
                generated_zone_refs=zoning.generated_zone_refs,
            )
            parts = (area.wkb, prepared.boundary.geometry.wkb)
        elif stage.name == BuildingStage.name and isinstance(config, BuildingStageConfig):
            blocks = _require(outputs, BlocksAndParcelsStage.name, BlocksAndParcelsStageOutput)
            footprints = self._polygons.read_buildings(snapshot=context.snapshot)
            value = BuildingStageInput(blocks=blocks, existing_footprints=footprints)
            parts = (
                *(ref.source_ref for ref in context.snapshot.buildings),
                *(item.geometry.wkb for item in footprints),
            )
        elif stage.name == DemographyStage.name and isinstance(config, DemographyStageConfig):
            if context.snapshot.demography:
                raise GenerationExecutionError("fixed population baseline hydration not implemented")
            buildings = _require(outputs, BuildingStage.name, BuildingStageOutput)
            value = DemographyStageInput(buildings=buildings)
        else:
            raise GenerationExecutionError(f"unsupported spatial stage or config: {stage.name}")
        return StageInvocation(
            stage_input=value, input_parts=parts,
            config_parts=(json.dumps(asdict(config), sort_keys=True, allow_nan=False,
                                     default=_json_default),),
        )


def build_spatial_registry(
    *, context: PipelineContext, constraint_engine: ConstraintEngine,
    session_factory: Callable[[], Session] = SessionLocal,
) -> StageRegistry:
    """First eight real stages. Never install this partial registry in deployed worker."""
    config = context.require_config(SuitabilityStage.name, SuitabilityStageConfig)
    factors: list[SuitabilityFactor] = [
        RunScopedDEMSlopeFactor(SqlAlchemyDEMSourceReader(
            artifact_store=context.ports.artifact_store, session_factory=session_factory,
        )),
        RunScopedRoadProximityFactor(SqlAlchemyFixedRoadSourceReader(
            session_factory=session_factory,
        )),
    ]
    if isinstance(config, PreparedSuitabilityStageConfig) and config.landuse_weights is not None:
        factors.append(RunScopedLanduseFactor(
            SqlAlchemyUrbanPolygonSourceReader(session_factory=session_factory),
            config.landuse_weights,
        ))
    supported = {factor.code for factor in factors}
    requested = {factor.code for factor in config.suitability.factors if factor.weight > 0}
    if not requested <= supported:
        raise GenerationExecutionError(f"unconfigured suitability factors: {requested - supported}")
    return StageRegistry(stages=(
        PrepareSnapshotStage(), ConstraintMaskStage(), SuitabilityStage(factors=tuple(factors)),
        ZoningStage(constraint_engine=constraint_engine), RoadStage(),
        BlocksAndParcelsStage(), BuildingStage(), DemographyStage(),
    ))
