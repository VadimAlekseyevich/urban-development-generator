from __future__ import annotations

import math
from dataclasses import dataclass

from core.urban_generator.buildings import BuildingArchetype
from core.urban_generator.domain import (
    CANONICAL_METRIC_REGISTRY,
    MetricSource,
    RawMetricId,
)
from core.urban_generator.stages.blocks import BlocksAndParcelsStageOutput
from core.urban_generator.stages.buildings import (
    BuildingStageBlockRef,
    BuildingStageOutput,
)
from core.urban_generator.suitability import WeightedSuitabilityResult
from core.urban_generator.zoning import ZoneClass


class LandBuildingMetricAdapterError(ValueError):
    """Raised when S11-T05 metric adapter inputs violate the source contract."""


LAND_BUILDING_RAW_METRIC_IDS = tuple(
    definition.metric_id
    for definition in CANONICAL_METRIC_REGISTRY.definitions_for(
        source=MetricSource.LAND_BUILDING
    )
)


@dataclass(frozen=True, slots=True)
class BuildingArchetypeShare:
    """One deterministic generated-building archetype distribution entry."""

    archetype: BuildingArchetype
    building_count: int
    share: float

    def __post_init__(self) -> None:
        if not isinstance(self.archetype, BuildingArchetype):
            raise LandBuildingMetricAdapterError(
                "archetype must be a BuildingArchetype value"
            )
        if (
            isinstance(self.building_count, bool)
            or not isinstance(self.building_count, int)
            or self.building_count < 0
        ):
            raise LandBuildingMetricAdapterError(
                "building_count must be a non-negative integer"
            )
        share = _require_non_negative_finite("archetype share", self.share)
        if share > 1.0:
            raise LandBuildingMetricAdapterError(
                "archetype share must stay inside 0..1"
            )
        object.__setattr__(self, "share", share)


@dataclass(frozen=True, slots=True)
class LandBuildingRawMetricValue:
    """One canonical S11-T05 land/building raw metric value."""

    metric_id: RawMetricId
    scalar_value: float | None = None
    archetype_distribution: tuple[BuildingArchetypeShare, ...] = ()

    def __post_init__(self) -> None:
        if self.metric_id not in LAND_BUILDING_RAW_METRIC_IDS:
            raise LandBuildingMetricAdapterError(
                f"not a land/building RawMetricId: {self.metric_id!r}"
            )

        if self.metric_id is RawMetricId.BUILDINGS_ARCHETYPE_DISTRIBUTION:
            if self.scalar_value is not None:
                raise LandBuildingMetricAdapterError(
                    "archetype distribution must not carry scalar_value"
                )
            if not isinstance(self.archetype_distribution, tuple):
                raise LandBuildingMetricAdapterError(
                    "archetype_distribution must be an immutable tuple"
                )
            expected = tuple(BuildingArchetype)
            actual = tuple(item.archetype for item in self.archetype_distribution)
            if actual != expected:
                raise LandBuildingMetricAdapterError(
                    "archetype distribution must contain every canonical "
                    "BuildingArchetype in declaration order"
                )
            total_count = sum(
                item.building_count for item in self.archetype_distribution
            )
            total_share = math.fsum(
                item.share for item in self.archetype_distribution
            )
            if total_count == 0:
                if total_share != 0.0:
                    raise LandBuildingMetricAdapterError(
                        "empty archetype distribution must have zero shares"
                    )
            elif not math.isclose(
                total_share,
                1.0,
                rel_tol=1e-12,
                abs_tol=1e-12,
            ):
                raise LandBuildingMetricAdapterError(
                    "non-empty archetype distribution shares must sum to one"
                )
            return

        if self.archetype_distribution:
            raise LandBuildingMetricAdapterError(
                "scalar land/building metric must not carry archetype distribution"
            )
        if self.scalar_value is None:
            raise LandBuildingMetricAdapterError(
                "scalar land/building metric requires scalar_value"
            )
        scalar = _require_non_negative_finite(
            "land/building scalar metric",
            self.scalar_value,
        )
        if self.metric_id in {
            RawMetricId.LAND_GREEN_RECREATION_SHARE,
            RawMetricId.BUILDINGS_COVERAGE_RATIO,
        } and scalar > 1.0:
            raise LandBuildingMetricAdapterError(
                f"{self.metric_id.value} must stay inside 0..1"
            )
        object.__setattr__(self, "scalar_value", scalar)


@dataclass(frozen=True, slots=True)
class LandBuildingMetricDiagnostics:
    developable_cell_count: int
    developed_block_area_m2: float
    recreation_block_area_m2: float
    associated_block_count: int
    generated_building_count: int
    fixed_building_ref_count: int
    archetype_distribution_includes_fixed: bool = False

    def __post_init__(self) -> None:
        for field_name in (
            "developable_cell_count",
            "associated_block_count",
            "generated_building_count",
            "fixed_building_ref_count",
        ):
            value = getattr(self, field_name)
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 0
            ):
                raise LandBuildingMetricAdapterError(
                    f"{field_name} must be a non-negative integer"
                )
        _require_non_negative_finite(
            "developed_block_area_m2",
            self.developed_block_area_m2,
        )
        _require_non_negative_finite(
            "recreation_block_area_m2",
            self.recreation_block_area_m2,
        )
        if self.recreation_block_area_m2 > self.developed_block_area_m2 + 1e-9:
            raise LandBuildingMetricAdapterError(
                "recreation block area cannot exceed developed block area"
            )
        if self.archetype_distribution_includes_fixed:
            raise LandBuildingMetricAdapterError(
                "fixed-building archetype distribution is not available in S11-T05"
            )


@dataclass(frozen=True, slots=True)
class LandBuildingRawMetricsResult:
    raw_metrics: tuple[LandBuildingRawMetricValue, ...]
    diagnostics: LandBuildingMetricDiagnostics

    def __post_init__(self) -> None:
        if not isinstance(self.raw_metrics, tuple):
            raise LandBuildingMetricAdapterError(
                "raw_metrics must be an immutable tuple"
            )
        if any(
            not isinstance(item, LandBuildingRawMetricValue)
            for item in self.raw_metrics
        ):
            raise LandBuildingMetricAdapterError(
                "raw_metrics must contain LandBuildingRawMetricValue values"
            )
        actual_ids = tuple(item.metric_id for item in self.raw_metrics)
        if actual_ids != LAND_BUILDING_RAW_METRIC_IDS:
            raise LandBuildingMetricAdapterError(
                "raw_metrics must contain every canonical land/building metric "
                "in registry order"
            )
        if not isinstance(self.diagnostics, LandBuildingMetricDiagnostics):
            raise LandBuildingMetricAdapterError(
                "diagnostics must be LandBuildingMetricDiagnostics"
            )

    def require(self, metric_id: RawMetricId) -> LandBuildingRawMetricValue:
        if metric_id not in LAND_BUILDING_RAW_METRIC_IDS:
            raise LandBuildingMetricAdapterError(
                f"not a land/building RawMetricId: {metric_id!r}"
            )
        return self.raw_metrics[LAND_BUILDING_RAW_METRIC_IDS.index(metric_id)]


class LandBuildingMetricAdapter:
    """Project authoritative suitability/block/building outputs into S11 raw metrics."""

    version = "1"

    def adapt(
        self,
        *,
        suitability: WeightedSuitabilityResult,
        blocks: BlocksAndParcelsStageOutput,
        buildings: BuildingStageOutput,
    ) -> LandBuildingRawMetricsResult:
        if not isinstance(suitability, WeightedSuitabilityResult):
            raise LandBuildingMetricAdapterError(
                "suitability must be WeightedSuitabilityResult"
            )
        if not isinstance(blocks, BlocksAndParcelsStageOutput):
            raise LandBuildingMetricAdapterError(
                "blocks must be BlocksAndParcelsStageOutput"
            )
        if not isinstance(buildings, BuildingStageOutput):
            raise LandBuildingMetricAdapterError(
                "buildings must be BuildingStageOutput"
            )

        self._validate_alignment(
            suitability=suitability,
            blocks=blocks,
            buildings=buildings,
        )

        cell_area_m2 = (
            suitability.grid.cell_width_m
            * suitability.grid.cell_height_m
        )
        developable_area_m2 = suitability.valid_count * cell_area_m2
        developed_area_m2 = blocks.cleanup.diagnostics.output_area_m2

        recreation_area_m2 = math.fsum(
            item.area_m2
            for item in buildings.blocks
            if item.zone_class is ZoneClass.RECREATION
        )
        if recreation_area_m2 > developed_area_m2 + 1e-9:
            raise LandBuildingMetricAdapterError(
                "recreation block area cannot exceed authoritative developed area"
            )
        green_recreation_share = (
            recreation_area_m2 / developed_area_m2
            if developed_area_m2 > 0.0
            else 0.0
        )

        summary = buildings.area_metrics.summary
        archetype_distribution = _archetype_distribution(buildings)

        values_by_id = {
            RawMetricId.LAND_DEVELOPABLE_AREA_M2: LandBuildingRawMetricValue(
                metric_id=RawMetricId.LAND_DEVELOPABLE_AREA_M2,
                scalar_value=developable_area_m2,
            ),
            RawMetricId.LAND_DEVELOPED_AREA_M2: LandBuildingRawMetricValue(
                metric_id=RawMetricId.LAND_DEVELOPED_AREA_M2,
                scalar_value=developed_area_m2,
            ),
            RawMetricId.LAND_GREEN_RECREATION_SHARE: LandBuildingRawMetricValue(
                metric_id=RawMetricId.LAND_GREEN_RECREATION_SHARE,
                scalar_value=green_recreation_share,
            ),
            RawMetricId.BUILDINGS_COVERAGE_RATIO: LandBuildingRawMetricValue(
                metric_id=RawMetricId.BUILDINGS_COVERAGE_RATIO,
                scalar_value=summary.coverage_ratio,
            ),
            RawMetricId.BUILDINGS_FAR: LandBuildingRawMetricValue(
                metric_id=RawMetricId.BUILDINGS_FAR,
                scalar_value=summary.far,
            ),
            RawMetricId.BUILDINGS_GFA_M2: LandBuildingRawMetricValue(
                metric_id=RawMetricId.BUILDINGS_GFA_M2,
                scalar_value=summary.total_gfa_m2,
            ),
            RawMetricId.BUILDINGS_ARCHETYPE_DISTRIBUTION: (
                LandBuildingRawMetricValue(
                    metric_id=RawMetricId.BUILDINGS_ARCHETYPE_DISTRIBUTION,
                    archetype_distribution=archetype_distribution,
                )
            ),
        }

        return LandBuildingRawMetricsResult(
            raw_metrics=tuple(
                values_by_id[metric_id]
                for metric_id in LAND_BUILDING_RAW_METRIC_IDS
            ),
            diagnostics=LandBuildingMetricDiagnostics(
                developable_cell_count=suitability.valid_count,
                developed_block_area_m2=developed_area_m2,
                recreation_block_area_m2=recreation_area_m2,
                associated_block_count=len(buildings.blocks),
                generated_building_count=len(buildings.attributes.buildings),
                fixed_building_ref_count=len(buildings.fixed_building_refs),
            ),
        )

    @staticmethod
    def _validate_alignment(
        *,
        suitability: WeightedSuitabilityResult,
        blocks: BlocksAndParcelsStageOutput,
        buildings: BuildingStageOutput,
    ) -> None:
        working_srid = suitability.grid.working_srid
        if blocks.cleanup.working_crs.srid != working_srid:
            raise LandBuildingMetricAdapterError(
                "block cleanup working_srid must match suitability working_srid"
            )
        if buildings.area_metrics.working_srid != working_srid:
            raise LandBuildingMetricAdapterError(
                "building area working_srid must match suitability working_srid"
            )

        block_ids = tuple(item.block_id for item in buildings.blocks)
        if block_ids != tuple(sorted(block_ids)) or len(block_ids) != len(
            set(block_ids)
        ):
            raise LandBuildingMetricAdapterError(
                "building block refs must be sorted and unique"
            )
        for item in buildings.blocks:
            if not isinstance(item, BuildingStageBlockRef):
                raise LandBuildingMetricAdapterError(
                    "building block refs must contain BuildingStageBlockRef values"
                )
            _require_non_negative_finite("building block area_m2", item.area_m2)
            if not isinstance(item.zone_class, ZoneClass):
                raise LandBuildingMetricAdapterError(
                    "building block zone_class must be ZoneClass"
                )

        attribute_ids = tuple(
            item.building_id for item in buildings.attributes.buildings
        )
        area_ids = tuple(
            item.building_id for item in buildings.area_metrics.buildings
        )
        if attribute_ids != area_ids:
            raise LandBuildingMetricAdapterError(
                "building attribute and area metric IDs must match exactly"
            )


def _archetype_distribution(
    buildings: BuildingStageOutput,
) -> tuple[BuildingArchetypeShare, ...]:
    total = len(buildings.attributes.buildings)
    counts = {
        archetype: sum(
            item.archetype is archetype
            for item in buildings.attributes.buildings
        )
        for archetype in BuildingArchetype
    }
    return tuple(
        BuildingArchetypeShare(
            archetype=archetype,
            building_count=counts[archetype],
            share=(counts[archetype] / total if total else 0.0),
        )
        for archetype in BuildingArchetype
    )


def _require_non_negative_finite(field_name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LandBuildingMetricAdapterError(
            f"{field_name} must be a finite non-negative number"
        )
    numeric = float(value)
    if not math.isfinite(numeric) or numeric < 0.0:
        raise LandBuildingMetricAdapterError(
            f"{field_name} must be a finite non-negative number"
        )
    return numeric
