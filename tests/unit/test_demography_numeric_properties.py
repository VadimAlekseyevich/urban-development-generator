from __future__ import annotations

import math
from itertools import permutations

import pytest

from core.urban_generator.buildings import (
    AssignedBuildingAttributes,
    BuildingArchetype,
    BuildingAreaMetrics,
    BuildingUse,
)
from core.urban_generator.demography import (
    AgeGroupAllocator,
    AgeGroupShare,
    BuildingAggregationRef,
    DemographicAggregator,
    DemographicScenario,
    EmploymentConfig,
    EmploymentEstimator,
    EmploymentSubject,
    JobDensityRule,
    PopulationAllocator,
    PopulationRasterSample,
    PopulationRasterSamplingResult,
    PopulationRasterValueKind,
    PopulationTarget,
    PopulationTargetKind,
    ResidentialBuildingCapacity,
    ResidentialCapacityCalculator,
    ResidentialCapacityResult,
    ResidentialCapacitySubject,
    SpatialDemographicCalibrator,
)
from core.urban_generator.zoning import ZoneClass


def _scenario(
    *,
    target_population: int = 25,
    residential_gfa_share: float = 0.6,
) -> DemographicScenario:
    return DemographicScenario(
        version="demography-v1",
        population_target=PopulationTarget(
            kind=PopulationTargetKind.TOTAL_POPULATION,
            value=target_population,
        ),
        occupancy_ratio=0.9,
        residential_area_per_person_m2=30.0,
        average_household_size=2.5,
        residential_gfa_share=residential_gfa_share,
        age_groups=(
            AgeGroupShare(code="child", min_age=0, max_age=17, share=0.23),
            AgeGroupShare(code="adult", min_age=18, max_age=64, share=0.51),
            AgeGroupShare(code="senior", min_age=65, max_age=None, share=0.26),
        ),
        working_population_ratio=0.6,
    )


def _employment_config() -> EmploymentConfig:
    return EmploymentConfig(
        version="employment-v1",
        rules=(
            JobDensityRule(
                use=BuildingUse.COMMERCIAL,
                area_per_job_m2=25.0,
            ),
            JobDensityRule(
                use=BuildingUse.MIXED,
                area_per_job_m2=20.0,
            ),
            JobDensityRule(
                use=BuildingUse.PUBLIC,
                area_per_job_m2=50.0,
            ),
        ),
    )


def _attributes(
    building_id: str,
    *,
    use: BuildingUse,
    zone_class: ZoneClass,
    floors: int = 2,
) -> AssignedBuildingAttributes:
    return AssignedBuildingAttributes(
        building_id=building_id,
        source_id="parcel:" + building_id,
        zone_class=zone_class,
        archetype=BuildingArchetype.POINT,
        use=use,
        floors=floors,
        config_version="buildings-v1",
    )


def _metrics(
    building_id: str,
    *,
    gfa_m2: float,
    floors: int = 2,
) -> BuildingAreaMetrics:
    return BuildingAreaMetrics(
        building_id=building_id,
        footprint_area_m2=gfa_m2 / floors,
        floors=floors,
        gfa_m2=gfa_m2,
    )


def _subjects() -> tuple[
    ResidentialCapacitySubject,
    EmploymentSubject,
    ResidentialCapacitySubject,
    EmploymentSubject,
    ResidentialCapacitySubject,
    EmploymentSubject,
]:
    residential_attr = _attributes(
        "building:a",
        use=BuildingUse.RESIDENTIAL,
        zone_class=ZoneClass.RESIDENTIAL,
    )
    mixed_attr = _attributes(
        "building:b",
        use=BuildingUse.MIXED,
        zone_class=ZoneClass.MIXED,
    )
    commercial_attr = _attributes(
        "building:c",
        use=BuildingUse.COMMERCIAL,
        zone_class=ZoneClass.MIXED,
    )
    residential_metrics = _metrics("building:a", gfa_m2=1_200.0)
    mixed_metrics = _metrics("building:b", gfa_m2=1_000.0)
    commercial_metrics = _metrics("building:c", gfa_m2=500.0)
    return (
        ResidentialCapacitySubject(
            metrics=residential_metrics,
            attributes=residential_attr,
        ),
        EmploymentSubject(
            metrics=residential_metrics,
            attributes=residential_attr,
        ),
        ResidentialCapacitySubject(
            metrics=mixed_metrics,
            attributes=mixed_attr,
        ),
        EmploymentSubject(
            metrics=mixed_metrics,
            attributes=mixed_attr,
        ),
        ResidentialCapacitySubject(
            metrics=commercial_metrics,
            attributes=commercial_attr,
        ),
        EmploymentSubject(
            metrics=commercial_metrics,
            attributes=commercial_attr,
        ),
    )


def _pipeline(
    capacity_subjects: tuple[ResidentialCapacitySubject, ...],
    employment_subjects: tuple[EmploymentSubject, ...],
):
    scenario = _scenario(target_population=25)
    capacities = ResidentialCapacityCalculator().calculate(
        capacity_subjects,
        scenario=scenario,
    )
    population = PopulationAllocator().allocate(
        capacities,
        scenario=scenario,
    )
    ages = AgeGroupAllocator().allocate(
        population,
        scenario=scenario,
    )
    employment = EmploymentEstimator().estimate(
        employment_subjects,
        population=population,
        scenario=scenario,
        config=_employment_config(),
    )
    refs = tuple(
        BuildingAggregationRef(
            building_id=subject.building_id,
            block_id=(
                "block:1"
                if subject.building_id in {"building:a", "building:b"}
                else "block:2"
            ),
            zone_id=(
                "zone:res"
                if subject.building_id == "building:a"
                else "zone:mixed"
            ),
            zone_class=(
                ZoneClass.RESIDENTIAL
                if subject.building_id == "building:a"
                else ZoneClass.MIXED
            ),
        )
        for subject in capacity_subjects
    )
    aggregation = DemographicAggregator().aggregate(
        refs,
        population=population,
        age_groups=ages,
        employment=employment,
    )
    return capacities, population, ages, employment, aggregation


def test_zero_gfa_capacity_is_safe_and_explicitly_unmet() -> None:
    scenario = _scenario(target_population=10)
    capacities = ResidentialCapacityResult(
        scenario_version=scenario.version,
        scenario_fingerprint=scenario.fingerprint,
        buildings=(
            ResidentialBuildingCapacity(
                building_id="building:zero",
                use=BuildingUse.RESIDENTIAL,
                total_gfa_m2=0.0,
                residential_gfa_m2=0.0,
                occupied_residential_area_m2=0.0,
                resident_capacity=0.0,
                household_capacity=0.0,
            ),
        ),
    )

    population = PopulationAllocator().allocate(
        capacities,
        scenario=scenario,
    )
    ages = AgeGroupAllocator().allocate(
        population,
        scenario=scenario,
    )

    assert population.diagnostics.allocatable_generated_capacity == 0
    assert population.diagnostics.allocated_generated_population == 0
    assert population.diagnostics.unmet_generated_population == 10
    assert population.allocations[0].utilization_ratio == 0.0
    assert ages.total_population == 0
    assert all(item.residents == 0 for item in ages.totals)
    assert all(math.isfinite(item.achieved_share) for item in ages.totals)


def test_mixed_use_gfa_is_partitioned_between_residents_and_jobs() -> None:
    scenario = _scenario(
        target_population=100,
        residential_gfa_share=0.6,
    )
    attr = _attributes(
        "building:mixed",
        use=BuildingUse.MIXED,
        zone_class=ZoneClass.MIXED,
    )
    metrics = _metrics("building:mixed", gfa_m2=1_000.0)
    capacity = ResidentialCapacityCalculator().calculate(
        (ResidentialCapacitySubject(metrics=metrics, attributes=attr),),
        scenario=scenario,
    )
    population = PopulationAllocator().allocate(
        capacity,
        scenario=scenario,
    )
    employment = EmploymentEstimator().estimate(
        (EmploymentSubject(metrics=metrics, attributes=attr),),
        population=population,
        scenario=scenario,
        config=_employment_config(),
    )

    residential_gfa = capacity.buildings[0].residential_gfa_m2
    job_gfa = employment.buildings[0].job_supporting_gfa_m2

    assert residential_gfa == pytest.approx(600.0)
    assert job_gfa == pytest.approx(400.0)
    assert residential_gfa + job_gfa == pytest.approx(metrics.gfa_m2)
    assert capacity.buildings[0].resident_capacity == pytest.approx(18.0)
    assert employment.buildings[0].jobs_estimate == pytest.approx(20.0)


@pytest.mark.parametrize(
    "target_population",
    [1, 2, 3, 7, 19, 25, 37, 101],
)
def test_target_and_cohort_sums_are_exact_for_numeric_cases(
    target_population: int,
) -> None:
    scenario = _scenario(target_population=target_population)
    capacities = ResidentialCapacityResult(
        scenario_version=scenario.version,
        scenario_fingerprint=scenario.fingerprint,
        buildings=(
            ResidentialBuildingCapacity(
                building_id="building:a",
                use=BuildingUse.RESIDENTIAL,
                total_gfa_m2=9_000.0,
                residential_gfa_m2=9_000.0,
                occupied_residential_area_m2=8_100.0,
                resident_capacity=270.0,
                household_capacity=108.0,
            ),
            ResidentialBuildingCapacity(
                building_id="building:b",
                use=BuildingUse.RESIDENTIAL,
                total_gfa_m2=6_000.0,
                residential_gfa_m2=6_000.0,
                occupied_residential_area_m2=5_400.0,
                resident_capacity=180.0,
                household_capacity=72.0,
            ),
        ),
    )

    population = PopulationAllocator().allocate(
        capacities,
        scenario=scenario,
    )
    ages = AgeGroupAllocator().allocate(
        population,
        scenario=scenario,
    )

    assert sum(item.residents for item in population.allocations) == target_population
    assert population.diagnostics.allocated_generated_population == target_population
    assert sum(item.residents for item in ages.totals) == target_population
    assert all(
        sum(group.residents for group in building.age_groups)
        == building.total_residents
        for building in ages.buildings
    )
    assert all(
        math.isfinite(group.achieved_share)
        for group in ages.totals
    )


def test_all_nodata_is_neutral_and_deterministic_for_spatial_calibration() -> None:
    capacity_subjects, employment_subjects = _pipeline_subject_groups()
    _, _, _, _, aggregation = _pipeline(
        capacity_subjects,
        employment_subjects,
    )
    samples = tuple(
        PopulationRasterSample(
            subject_id=block.block_id,
            requested_cell_count=4,
            valid_cell_count=0,
            nodata_cell_count=4,
            sampled_area_m2=0.0,
            sampled_population=0.0,
            mean_density_per_km2=0.0,
        )
        for block in aggregation.blocks
    )
    raster = PopulationRasterSamplingResult(
        working_srid=3857,
        value_kind=PopulationRasterValueKind.POPULATION_PER_CELL,
        cell_area_m2=100.0,
        samples=samples,
    )
    calibrator = SpatialDemographicCalibrator()

    first = calibrator.calibrate(aggregation, raster=raster)
    second = calibrator.calibrate(aggregation, raster=raster)

    assert first == second
    assert first.aggregation == aggregation
    assert first.diagnostics.evidence_block_count == 0
    assert first.diagnostics.moved_population == 0
    assert first.diagnostics.fallback_zone_count == len(aggregation.zones)


def _pipeline_subject_groups() -> tuple[
    tuple[ResidentialCapacitySubject, ...],
    tuple[EmploymentSubject, ...],
]:
    (
        res_capacity,
        res_employment,
        mixed_capacity,
        mixed_employment,
        commercial_capacity,
        commercial_employment,
    ) = _subjects()
    return (
        (res_capacity, mixed_capacity, commercial_capacity),
        (res_employment, mixed_employment, commercial_employment),
    )


def test_full_demography_pipeline_is_permutation_deterministic() -> None:
    capacity_subjects, employment_subjects = _pipeline_subject_groups()
    baseline = _pipeline(capacity_subjects, employment_subjects)

    for order in permutations(range(len(capacity_subjects))):
        permuted_capacity = tuple(capacity_subjects[index] for index in order)
        permuted_employment = tuple(employment_subjects[index] for index in order)
        candidate = _pipeline(permuted_capacity, permuted_employment)
        assert candidate == baseline

    _, population, ages, employment, aggregation = baseline
    assert population.diagnostics.allocated_generated_population == 25
    assert ages.total_population == 25
    assert aggregation.totals.population == 25
    assert aggregation.totals.jobs_estimate == pytest.approx(
        employment.summary.total_jobs_estimate
    )
    assert sum(item.residents for item in aggregation.totals.age_groups) == 25
