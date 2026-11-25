"""Versioned initial planning assumptions, independent of test-only smoke configs."""

from __future__ import annotations

import json
from dataclasses import fields, is_dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.app.adapters.stage_config_resolver import (
    COMPLETE_CONFIG_SCHEMA_VERSION,
    CanonicalStageConfigResolver,
)
from core.urban_generator.blocks import (
    BlockFrontagePolicy,
    OversizedBlockSplitPolicy,
    ParcelSubdivisionPolicy,
    SliverCleanupPolicy,
)
from core.urban_generator.buildings import (
    BuildingArchetype,
    BuildingArchetypeConfig,
    BuildingAttributeConfig,
    BuildingAttributeRule,
    BuildingConfig,
    BuildingFootprintStrategy,
    BuildingPlacementCandidatePolicy,
    BuildingPlacementScope,
    BuildingSpacingPolicy,
    BuildingUse,
    RectangularPointFootprintSpec,
)
from core.urban_generator.constraints import AggregateMetricBound
from core.urban_generator.demography import (
    AgeGroupShare,
    DemographicDemandCategory,
    DemographicScenario,
    EmploymentConfig,
    JobDensityRule,
    PopulationTarget,
    PopulationTargetKind,
)
from core.urban_generator.domain import ConfigRef, MetricDirection, RawMetricId
from core.urban_generator.infrastructure import (
    ExistingFacilityMappingRule,
    InfrastructureCandidatePolicy,
    InfrastructureCandidateSource,
    InfrastructureCategory,
    InfrastructureDemandModel,
    InfrastructureNetworkSnapPolicy,
    InfrastructureType,
)
from core.urban_generator.metrics import (
    CompositeScoreConfig,
    CompositeScoreMetricWeight,
    MetricNormalizationPolicy,
    MetricNormalizationProfile,
    NormalizationClampPolicy,
    NormalizationMissingPolicy,
)
from core.urban_generator.roads import (
    CandidateRoadAnchorPolicy,
    LeastCostConnectorPolicy,
    RoadValidationPolicy,
)
from core.urban_generator.roads.endpoint_snapping import EndpointRoadSnappingPolicy
from core.urban_generator.roads.fixed_network_attachment import FixedNetworkAttachmentPolicy
from core.urban_generator.stages import (
    BlocksAndParcelsStageConfig,
    BuildingArchetypeStageSpec,
    BuildingStageConfig,
    BuildingZoneTarget,
    ConstraintMaskStageConfig,
    DemographyStageConfig,
    InfrastructureStageConfig,
    PrepareSnapshotStageConfig,
    RoadStageConfig,
    ZoningStageConfig,
)
from core.urban_generator.stages.final_evaluation import (
    FinalValidationStageConfig,
    MetricsStageConfig,
)
from core.urban_generator.stages.persist_manifest import PersistManifestStageConfig
from core.urban_generator.stages.suitability import PreparedSuitabilityStageConfig
from core.urban_generator.suitability import (
    LanduseClassWeight,
    LanduseClassWeights,
    SuitabilityConfig,
    SuitabilityFactorConfig,
    SuitabilityNormalization,
    SuitabilityThresholds,
)
from core.urban_generator.zoning import ZoneClass, ZoneClassConfig, ZoningConfig


class PlanningParameters(BaseModel):
    """Bounded inputs for the initial neighbourhood-scale planning profile."""

    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    cell_size_m: float = Field(default=10, ge=2, le=100)
    suitability_source: Literal["slope", "landuse"] = "slope"
    residential_percent: int = Field(default=60, ge=1, le=100)
    mixed_percent: int = Field(default=20, ge=0, le=99)
    public_percent: int = Field(default=10, ge=0, le=99)
    recreation_percent: int = Field(default=10, ge=0, le=99)
    zoning_seeds: int = Field(default=16, ge=1, le=128)
    road_anchors: int = Field(default=24, ge=3, le=64)
    max_block_area_m2: float = Field(default=20000, ge=400, le=100000)
    floors: int = Field(default=4, ge=1, le=16)
    coverage_ratio: float = Field(default=0.25, ge=0.05, le=0.6)
    target_population: int = Field(default=1000, ge=1, le=1_000_000)
    area_per_person_m2: float = Field(default=30, ge=10, le=100)
    clinic_distance_m: float = Field(default=800, ge=100, le=3000)
    clinic_capacity: int = Field(default=1000, ge=50, le=10000)

    @model_validator(mode="after")
    def validate_shares(self) -> PlanningParameters:
        if sum((
            self.residential_percent, self.mixed_percent,
            self.public_percent, self.recreation_percent,
        )) != 100:
            raise ValueError("zone percentages must sum to 100")
        return self


def _encode(value: Any) -> Any:
    if is_dataclass(value):
        return {field.name: _encode(getattr(value, field.name))
                for field in fields(value) if field.init}
    if isinstance(value, (tuple, list, frozenset)):
        items = sorted(value, key=str) if isinstance(value, frozenset) else value
        return [_encode(item) for item in items]
    if isinstance(value, dict):
        return {key: _encode(item) for key, item in value.items()}
    return value


def build_planning_config(p: PlanningParameters) -> dict[str, Any]:
    """Build and validate the actual twelve-stage envelope without DB writes.

    This is a pilot scenario, not a jurisdictional standard or success promise.
    It uses one rectangular point archetype; recreation remains unbuilt.
    """
    shares = {
        ZoneClass.RESIDENTIAL: p.residential_percent,
        ZoneClass.MIXED: p.mixed_percent,
        ZoneClass.PUBLIC: p.public_percent,
        ZoneClass.RECREATION: p.recreation_percent,
    }
    uses = {
        ZoneClass.RESIDENTIAL: BuildingUse.RESIDENTIAL,
        ZoneClass.MIXED: BuildingUse.MIXED,
        ZoneClass.PUBLIC: BuildingUse.PUBLIC,
    }
    buildable = tuple(uses)
    zoning = ZoningConfig(
        version="neighbourhood-v1",
        zones=tuple(
            ZoneClassConfig(zone, percent / 100, 100)
            for zone, percent in shares.items()
        ),
    )
    if p.suitability_source == "slope":
        factor = SuitabilityFactorConfig(
            code="slope", weight=1, normalization=SuitabilityNormalization.INVERTED_MIN_MAX,
            raw_min=0, raw_max=30,
        )
        landuse_weights: LanduseClassWeights | None = None
    else:
        factor = SuitabilityFactorConfig(
            code="landuse", weight=1, normalization=SuitabilityNormalization.IDENTITY,
        )
        landuse_weights = LanduseClassWeights(
            version="neighbourhood-v1",
            classes=(
                LanduseClassWeight("residential", 1),
                LanduseClassWeight("grass", 0.8),
                LanduseClassWeight("meadow", 0.8),
                LanduseClassWeight("farmland", 0.5),
            ),
        )
    configs = (
        ("prepare_snapshot", PrepareSnapshotStageConfig(cell_size_m=p.cell_size_m)),
        ("evaluate_constraints", ConstraintMaskStageConfig()),
        ("suitability", PreparedSuitabilityStageConfig(
            suitability=SuitabilityConfig(
                version="neighbourhood-v1", factors=(factor,),
                thresholds=SuitabilityThresholds(minimum_score=0.2),
            ),
            landuse_weights=landuse_weights,
        )),
        ("zoning", ZoningStageConfig(zoning=zoning, seed_count=p.zoning_seeds)),
        ("roads", RoadStageConfig(
            endpoint_snapping=EndpointRoadSnappingPolicy(tolerance_m=1),
            anchor_policy=CandidateRoadAnchorPolicy(
                max_candidates=p.road_anchors, max_sampled_cells=100_000,
                allowed_zone_classes=buildable, spatially_balanced=True,
            ),
            least_cost_policy=LeastCostConnectorPolicy(max_visited_cells=100_000),
            validation_policy=RoadValidationPolicy(
                max_component_count=1, max_dead_end_ratio=1,
            ),
            fixed_attachment_policy=FixedNetworkAttachmentPolicy(max_distance_m=500),
        )),
        ("blocks_and_parcels", BlocksAndParcelsStageConfig(
            frontage_policy=BlockFrontagePolicy(
                access_tolerance_m=2, minimum_frontage_m=8,
            ),
            split_policy=OversizedBlockSplitPolicy(max_area_m2=p.max_block_area_m2),
            sliver_policy=SliverCleanupPolicy(min_area_m2=100),
            parcel_policy=ParcelSubdivisionPolicy(
                target_frontage_m=25, minimum_frontage_m=8, minimum_parcel_area_m2=200,
            ),
        )),
        ("buildings", BuildingStageConfig(
            building=BuildingConfig(
                version="neighbourhood-v1",
                archetypes=(BuildingArchetypeConfig(
                    archetype=BuildingArchetype.POINT,
                    footprint_strategy=BuildingFootprintStrategy.RECTANGULAR_POINT,
                    placement_scope=BuildingPlacementScope.PARCEL_OR_BLOCK,
                    allowed_zones=buildable,
                ),),
            ),
            attributes=BuildingAttributeConfig(
                version="neighbourhood-v1",
                rules=tuple(
                    BuildingAttributeRule(zone, BuildingArchetype.POINT, use, p.floors, p.floors)
                    for zone, use in uses.items()
                ),
            ),
            archetype_specs=(BuildingArchetypeStageSpec(
                archetype=BuildingArchetype.POINT,
                footprint_spec=RectangularPointFootprintSpec.point(size_m=12),
                planning_floor_area_multiplier=p.floors,
            ),),
            zone_targets=tuple(
                BuildingZoneTarget(
                    zone_class=zone, target_coverage_ratio=p.coverage_ratio,
                    target_far=p.coverage_ratio * p.floors,
                )
                for zone in buildable
            ),
            candidate_policy=BuildingPlacementCandidatePolicy(
                grid_spacing_m=16, frontage_spacing_m=16, include_frontage=False,
            ),
            spacing_policy=BuildingSpacingPolicy(minimum_gap_m=4),
        )),
        ("demography", DemographyStageConfig(
            scenario=DemographicScenario(
                version="neighbourhood-v1",
                population_target=PopulationTarget(
                    kind=PopulationTargetKind.TOTAL_POPULATION, value=p.target_population,
                ),
                occupancy_ratio=0.9, residential_area_per_person_m2=p.area_per_person_m2,
                average_household_size=2.4, residential_gfa_share=0.8,
                age_groups=(
                    AgeGroupShare("child", 0, 17, 0.2),
                    AgeGroupShare("adult", 18, 64, 0.65),
                    AgeGroupShare("senior", 65, None, 0.15),
                ),
                working_population_ratio=0.5,
            ),
            employment=EmploymentConfig(
                version="neighbourhood-v1",
                rules=(
                    JobDensityRule(use=BuildingUse.MIXED, area_per_job_m2=50),
                    JobDensityRule(use=BuildingUse.PUBLIC, area_per_job_m2=40),
                    JobDensityRule(use=BuildingUse.COMMERCIAL, area_per_job_m2=30),
                ),
            ),
        )),
        ("infrastructure", InfrastructureStageConfig(
            infrastructure_types=(InfrastructureType(
                version="neighbourhood-v1", code="clinic.primary",
                category=InfrastructureCategory.HEALTHCARE,
                demand_model=InfrastructureDemandModel(
                    signal=DemographicDemandCategory.POPULATION, demand_rate=1,
                ),
                capacity=p.clinic_capacity, max_network_distance_m=p.clinic_distance_m,
                allowed_zones=buildable, minimum_site_area_m2=100, target_site_area_m2=144,
                candidate_policy=InfrastructureCandidatePolicy(
                    sources=(InfrastructureCandidateSource.BUILDING,),
                ),
            ),),
            snap_policy=InfrastructureNetworkSnapPolicy(max_snap_distance_m=100),
            mapping_rules=(ExistingFacilityMappingRule("clinic", "clinic.primary"),),
        )),
        ("final_validation", FinalValidationStageConfig(hard_bounds=(
            AggregateMetricBound(RawMetricId.BUILDINGS_COVERAGE_RATIO, minimum=0, maximum=1),
            AggregateMetricBound(RawMetricId.BUILDINGS_FAR, minimum=0, maximum=float(p.floors)),
            AggregateMetricBound(RawMetricId.DEMOGRAPHY_DENSITY_PER_KM2, minimum=0),
            AggregateMetricBound(
                RawMetricId.INFRASTRUCTURE_CAPACITY_UTILIZATION, minimum=0, maximum=1,
            ),
        ))),
        ("metrics", MetricsStageConfig(
            normalization_profile=MetricNormalizationProfile(
                profile_id="neighbourhood-v1", version="1",
                policies=(MetricNormalizationPolicy(
                    metric_id=RawMetricId.CONSTRAINTS_HARD_VIOLATION_COUNT,
                    direction=MetricDirection.LOWER_IS_BETTER, lower_bound=0, upper_bound=10,
                    version="1", clamp_policy=NormalizationClampPolicy.CLAMP,
                    missing_policy=NormalizationMissingPolicy.REJECT,
                ),),
            ),
            score=CompositeScoreConfig(
                config_id="neighbourhood-v1", version="1",
                weights=(CompositeScoreMetricWeight(
                    RawMetricId.CONSTRAINTS_HARD_VIOLATION_COUNT, 1,
                ),),
            ),
        )),
        ("persist_manifest", PersistManifestStageConfig()),
    )
    payload: dict[str, Any] = json.loads(json.dumps({
        "schema_version": COMPLETE_CONFIG_SCHEMA_VERSION,
        "stages": {
            name: {"stage_version": "1.0.0", "config": _encode(config)}
            for name, config in configs
        },
    }, allow_nan=False))
    CanonicalStageConfigResolver(schema_version=COMPLETE_CONFIG_SCHEMA_VERSION).resolve(
        config_json=payload, schema_version=COMPLETE_CONFIG_SCHEMA_VERSION,
        source=ConfigRef(name="generation", ref="neighbourhood-v1"),
    )
    return payload
