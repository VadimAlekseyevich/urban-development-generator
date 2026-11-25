"""Small deterministic config for integration/browser smoke tests only.

Not a planning preset or a promise of generation success on arbitrary inputs.
"""

from __future__ import annotations

import json
from dataclasses import fields, is_dataclass

from backend.app.adapters.stage_config_resolver import COMPLETE_CONFIG_SCHEMA_VERSION
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
from core.urban_generator.domain import MetricDirection, RawMetricId
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


def _zoning_config() -> ZoningConfig:
    return ZoningConfig(
        version="m0-v1",
        zones=tuple(
            ZoneClassConfig(
                zone_class=zone_class,
                target_share=1.0 if zone_class is ZoneClass.RESIDENTIAL else 0.0,
                minimum_area_m2=0.1,
            )
            for zone_class in ZoneClass
        ),
    )


def _road_config() -> RoadStageConfig:
    return RoadStageConfig(
        endpoint_snapping=EndpointRoadSnappingPolicy(tolerance_m=0.05),
        anchor_policy=CandidateRoadAnchorPolicy(
            max_candidates=1,
            max_sampled_cells=100,
            allowed_zone_classes=(ZoneClass.RESIDENTIAL,),
        ),
        least_cost_policy=LeastCostConnectorPolicy(max_visited_cells=10_000),
        validation_policy=RoadValidationPolicy(
            max_component_count=1,
            max_dead_end_ratio=1.0,
        ),
        fixed_attachment_policy=FixedNetworkAttachmentPolicy(
            max_distance_m=20.0
        ),
    )


def _blocks_config() -> BlocksAndParcelsStageConfig:
    return BlocksAndParcelsStageConfig(
        frontage_policy=BlockFrontagePolicy(
            access_tolerance_m=0.0,
            minimum_frontage_m=1.0,
        ),
        split_policy=OversizedBlockSplitPolicy(max_area_m2=200.0),
        sliver_policy=SliverCleanupPolicy(min_area_m2=1.0),
        parcel_policy=ParcelSubdivisionPolicy(
            target_frontage_m=20.0,
            minimum_frontage_m=1.0,
            minimum_parcel_area_m2=10.0,
        ),
    )


def _building_config() -> BuildingStageConfig:
    return BuildingStageConfig(
        building=BuildingConfig(
            version="m0-buildings-v1",
            archetypes=(
                BuildingArchetypeConfig(
                    archetype=BuildingArchetype.POINT,
                    footprint_strategy=(
                        BuildingFootprintStrategy.RECTANGULAR_POINT
                    ),
                    placement_scope=BuildingPlacementScope.PARCEL,
                    allowed_zones=(ZoneClass.RESIDENTIAL,),
                ),
            ),
        ),
        attributes=BuildingAttributeConfig(
            version="m0-attributes-v1",
            rules=(
                BuildingAttributeRule(
                    zone_class=ZoneClass.RESIDENTIAL,
                    archetype=BuildingArchetype.POINT,
                    use=BuildingUse.RESIDENTIAL,
                    min_floors=2,
                    max_floors=2,
                ),
            ),
        ),
        archetype_specs=(
            BuildingArchetypeStageSpec(
                archetype=BuildingArchetype.POINT,
                footprint_spec=RectangularPointFootprintSpec.point(size_m=2.0),
                planning_floor_area_multiplier=2.0,
            ),
        ),
        zone_targets=(
            BuildingZoneTarget(
                zone_class=ZoneClass.RESIDENTIAL,
                target_coverage_ratio=0.16,
                target_far=0.32,
                coverage_tolerance=0.0,
                far_tolerance=0.0,
            ),
        ),
        candidate_policy=BuildingPlacementCandidatePolicy(
            grid_spacing_m=5.0,
            frontage_spacing_m=5.0,
            include_frontage=False,
        ),
        spacing_policy=BuildingSpacingPolicy(minimum_gap_m=0.0),
    )


def _demography_config() -> DemographyStageConfig:
    return DemographyStageConfig(
        scenario=DemographicScenario(
            version="m0-demography-v1",
            population_target=PopulationTarget(
                kind=PopulationTargetKind.TOTAL_POPULATION,
                value=8,
            ),
            occupancy_ratio=1.0,
            residential_area_per_person_m2=1.0,
            average_household_size=2.0,
            residential_gfa_share=1.0,
            age_groups=(
                AgeGroupShare(
                    code="child",
                    min_age=0,
                    max_age=17,
                    share=0.25,
                ),
                AgeGroupShare(
                    code="adult",
                    min_age=18,
                    max_age=None,
                    share=0.75,
                ),
            ),
            working_population_ratio=0.5,
        ),
        employment=EmploymentConfig(
            version="m0-jobs-v1",
            rules=(
                JobDensityRule(
                    use=BuildingUse.MIXED,
                    area_per_job_m2=50.0,
                ),
                JobDensityRule(
                    use=BuildingUse.PUBLIC,
                    area_per_job_m2=40.0,
                ),
                JobDensityRule(
                    use=BuildingUse.COMMERCIAL,
                    area_per_job_m2=30.0,
                ),
            ),
        ),
    )


def smoke_generation_config(*, reject_hard: bool = False) -> dict[str, object]:
    configs = (
        ("prepare_snapshot", PrepareSnapshotStageConfig(cell_size_m=1)),
        ("evaluate_constraints", ConstraintMaskStageConfig()),
        ("suitability", PreparedSuitabilityStageConfig(
            suitability=SuitabilityConfig(
                version="v1",
                factors=(SuitabilityFactorConfig(
                    code="landuse", weight=1, normalization=SuitabilityNormalization.IDENTITY,
                ),),
                thresholds=SuitabilityThresholds(minimum_score=0),
            ),
            landuse_weights=LanduseClassWeights(
                version="v1", classes=(LanduseClassWeight("residential", 1),),
            ),
        )),
        ("zoning", ZoningStageConfig(zoning=_zoning_config(), seed_count=1)),
        ("roads", _road_config()),
        ("blocks_and_parcels", _blocks_config()),
        ("buildings", _building_config()),
        ("demography", _demography_config()),
        ("infrastructure", InfrastructureStageConfig(
            infrastructure_types=(InfrastructureType(
                version="v1", code="clinic.primary", category=InfrastructureCategory.HEALTHCARE,
                demand_model=InfrastructureDemandModel(
                    signal=DemographicDemandCategory.POPULATION, demand_rate=1,
                ),
                capacity=8, max_network_distance_m=100,
                allowed_zones=(ZoneClass.RESIDENTIAL,),
                minimum_site_area_m2=1, target_site_area_m2=2,
                candidate_policy=InfrastructureCandidatePolicy(
                    sources=(InfrastructureCandidateSource.BUILDING,),
                ),
            ),),
            snap_policy=InfrastructureNetworkSnapPolicy(max_snap_distance_m=100),
            mapping_rules=(ExistingFacilityMappingRule("clinic", "clinic.primary"),),
        )),
        ("final_validation", FinalValidationStageConfig(hard_bounds=(
            AggregateMetricBound(RawMetricId.BUILDINGS_COVERAGE_RATIO, minimum=0, maximum=1),
            AggregateMetricBound(
                RawMetricId.BUILDINGS_FAR,
                minimum=1000 if reject_hard else 0,
                maximum=2000 if reject_hard else 10,
            ),
            AggregateMetricBound(RawMetricId.DEMOGRAPHY_DENSITY_PER_KM2, minimum=0),
            AggregateMetricBound(RawMetricId.INFRASTRUCTURE_CAPACITY_UTILIZATION,
                                 minimum=0, maximum=1),
        ))),
        ("metrics", MetricsStageConfig(
            normalization_profile=MetricNormalizationProfile(
                profile_id="real-chain", version="1",
                policies=(MetricNormalizationPolicy(
                    metric_id=RawMetricId.CONSTRAINTS_HARD_VIOLATION_COUNT,
                    direction=MetricDirection.LOWER_IS_BETTER,
                    lower_bound=0, upper_bound=10, version="1",
                    clamp_policy=NormalizationClampPolicy.CLAMP,
                    missing_policy=NormalizationMissingPolicy.REJECT,
                ),),
            ),
            score=CompositeScoreConfig(
                config_id="real-chain", version="1",
                weights=(CompositeScoreMetricWeight(
                    RawMetricId.CONSTRAINTS_HARD_VIOLATION_COUNT, 1,
                ),),
            ),
        )),
    )
    configs += (("persist_manifest", PersistManifestStageConfig()),)

    def encode(value):
        if is_dataclass(value):
            return {field.name: encode(getattr(value, field.name))
                    for field in fields(value) if field.init}
        if isinstance(value, (tuple, list, frozenset)):
            items = sorted(value, key=str) if isinstance(value, frozenset) else value
            return [encode(item) for item in items]
        if isinstance(value, dict):
            return {key: encode(item) for key, item in value.items()}
        return value

    return json.loads(json.dumps({
        "schema_version": COMPLETE_CONFIG_SCHEMA_VERSION,
        "stages": {name: {"stage_version": "1.0.0", "config": encode(cfg)}
                   for name, cfg in configs},
    }, allow_nan=False))


if __name__ == "__main__":
    print(json.dumps(smoke_generation_config(), allow_nan=False))
