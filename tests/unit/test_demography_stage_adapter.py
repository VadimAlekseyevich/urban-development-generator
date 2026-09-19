import uuid

from shapely.geometry import box

from core.urban_generator.buildings import (
    AssignedBuildingAttributes,
    BuildingArchetype,
    BuildingAreaMetricsCalculator,
    BuildingAreaSubject,
    BuildingAttributeAssignmentResult,
    BuildingUse,
)
from core.urban_generator.demography import (
    AgeGroupShare,
    DemographicDemandCategory,
    DemographicScenario,
    EmploymentConfig,
    JobDensityRule,
    PopulationTarget,
    PopulationTargetKind,
)
from core.urban_generator.domain import (
    ConfigRef,
    CorrelationMetadata,
    ProjectRef,
    ProjectSettings,
    RunContext,
    RunMode,
    SnapshotLayerKind,
    SnapshotLayerRef,
    TerritorySnapshot,
)
from core.urban_generator.stages.buildings import (
    BuildingStageBlockRef,
    BuildingStageOutput,
    BuildingStageOwnershipRef,
)
from core.urban_generator.stages.demography import (
    DemographyStage,
    DemographyStageConfig,
    DemographyStageInput,
)
from core.urban_generator.zoning import ZoneClass

WORKING_SRID = 32637
RUN_ID = uuid.UUID("00000000-0000-0000-0000-000000000666")


def _snapshot() -> TerritorySnapshot:
    return TerritorySnapshot(
        snapshot_id=uuid.UUID("00000000-0000-0000-0000-000000000111"),
        project=ProjectRef(
            project_id=uuid.UUID("00000000-0000-0000-0000-000000000222")
        ),
        settings=ProjectSettings(working_srid=WORKING_SRID),
        boundary=SnapshotLayerRef(
            kind=SnapshotLayerKind.BOUNDARY,
            source_ref="synthetic:boundary:v1",
        ),
    )


def _context() -> RunContext:
    return RunContext(
        run_id=RUN_ID,
        mode=RunMode.FROM_SCRATCH,
        seed=2026,
        working_srid=WORKING_SRID,
        config_refs=(ConfigRef(name="demography", ref="synthetic:v1"),),
        correlation=CorrelationMetadata(correlation_id="demography-stage-test"),
    )


def _buildings() -> BuildingStageOutput:
    attributes = AssignedBuildingAttributes(
        building_id="building:one",
        source_id="parcel:one",
        zone_class=ZoneClass.RESIDENTIAL,
        archetype=BuildingArchetype.POINT,
        use=BuildingUse.RESIDENTIAL,
        floors=2,
        config_version="attrs-v1",
    )
    assignment = BuildingAttributeAssignmentResult(
        config_version="attrs-v1",
        config_fingerprint="a" * 64,
        buildings=(attributes,),
    )
    subjects = (
        BuildingAreaSubject(
            building_id="building:one",
            geometry=box(0.0, 0.0, 10.0, 10.0),
            attributes=attributes,
            working_srid=WORKING_SRID,
        ),
    )
    area = BuildingAreaMetricsCalculator(
        working_srid=WORKING_SRID
    ).calculate(
        subjects,
        site_area_m2=1_000.0,
    )
    return BuildingStageOutput(
        sources=(),
        accepted_proposals=(),
        ownership=(
            BuildingStageOwnershipRef(
                building_id="building:one",
                source_id="parcel:one",
                block_id="block:one",
                zone_id="zone:one",
                zone_class=ZoneClass.RESIDENTIAL,
            ),
        ),
        blocks=(
            BuildingStageBlockRef(
                block_id="block:one",
                zone_id="zone:one",
                zone_class=ZoneClass.RESIDENTIAL,
                area_m2=1_000.0,
            ),
        ),
        attributes=assignment,
        area_subjects=subjects,
        area_metrics=area,
        fixed_building_refs=(),
    )


def _config() -> DemographyStageConfig:
    return DemographyStageConfig(
        scenario=DemographicScenario(
            version="demo-v1",
            population_target=PopulationTarget(
                kind=PopulationTargetKind.TOTAL_POPULATION,
                value=8,
            ),
            occupancy_ratio=1.0,
            residential_area_per_person_m2=25.0,
            average_household_size=2.0,
            residential_gfa_share=0.5,
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
            version="jobs-v1",
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


def test_demography_stage_composes_s09_pipeline_deterministically() -> None:
    stage = DemographyStage()
    snapshot = _snapshot()
    context = _context()
    stage_input = DemographyStageInput(buildings=_buildings())

    result = stage.execute(
        snapshot=snapshot,
        context=context,
        stage_input=stage_input,
        config=_config(),
    )

    assert result.output.population.diagnostics.allocated_generated_population == 8
    assert result.output.population.diagnostics.unmet_generated_population == 0
    assert result.output.age_groups.total_population == 8
    assert result.output.employment.summary.total_jobs_estimate == 0.0
    assert result.output.aggregation.totals.population == 8
    assert result.output.metrics.totals.population == 8
    assert result.output.metrics.blocks[0].population_density_per_km2 == 8_000.0
    assert result.output.demand_profile.totals.signal(
        DemographicDemandCategory.WORKFORCE
    ).value == 4.0
    assert result.output.calibration is None
    assert result.output.fixed_demography_refs == ()

    repeated = stage.execute(
        snapshot=snapshot,
        context=context,
        stage_input=stage_input,
        config=_config(),
    )
    assert repeated.fingerprint == result.fingerprint
    assert repeated.output.demand_profile == result.output.demand_profile
