from __future__ import annotations

import pytest

from core.urban_generator.buildings import (
    AssignedBuildingAttributes,
    BuildingArchetype,
    BuildingAreaMetrics,
    BuildingUse,
)
from core.urban_generator.demography import (
    AgeGroupShare,
    BuildingPopulationAllocation,
    DemographicScenario,
    EmploymentConfig,
    EmploymentEstimateError,
    EmploymentEstimator,
    EmploymentSubject,
    JobDensityRule,
    PopulationAllocationDiagnostics,
    PopulationAllocationResult,
    PopulationAllocationStatus,
    PopulationTarget,
    PopulationTargetKind,
)
from core.urban_generator.zoning import ZoneClass


def _scenario(*, working_ratio: float = 0.6) -> DemographicScenario:
    return DemographicScenario(
        version="demography-v1",
        population_target=PopulationTarget(
            kind=PopulationTargetKind.TOTAL_POPULATION,
            value=100,
        ),
        occupancy_ratio=0.9,
        residential_area_per_person_m2=30.0,
        average_household_size=2.5,
        residential_gfa_share=0.6,
        age_groups=(
            AgeGroupShare(code="0-17", min_age=0, max_age=17, share=0.2),
            AgeGroupShare(code="18+", min_age=18, max_age=None, share=0.8),
        ),
        working_population_ratio=working_ratio,
    )


def _config() -> EmploymentConfig:
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


def _subject(
    building_id: str,
    *,
    use: BuildingUse,
    gfa_m2: float = 1_000.0,
    floors: int = 2,
) -> EmploymentSubject:
    return EmploymentSubject(
        metrics=BuildingAreaMetrics(
            building_id=building_id,
            footprint_area_m2=gfa_m2 / floors,
            floors=floors,
            gfa_m2=gfa_m2,
        ),
        attributes=AssignedBuildingAttributes(
            building_id=building_id,
            source_id="parcel:1",
            zone_class=ZoneClass.RESIDENTIAL,
            archetype=BuildingArchetype.POINT,
            use=use,
            floors=floors,
            config_version="buildings-v1",
        ),
    )


def _population(
    scenario: DemographicScenario,
    *,
    allocated: int = 100,
    target: int = 100,
) -> PopulationAllocationResult:
    status = (
        PopulationAllocationStatus.TARGET_MET
        if allocated == target
        else PopulationAllocationStatus.CAPACITY_EXHAUSTED
    )
    return PopulationAllocationResult(
        scenario_version=scenario.version,
        scenario_fingerprint=scenario.fingerprint,
        allocations=(
            BuildingPopulationAllocation(
                building_id="building:residents",
                resident_capacity=float(allocated),
                integer_capacity=allocated,
                residents=allocated,
                utilization_ratio=1.0 if allocated > 0 else 0.0,
            ),
        ),
        diagnostics=PopulationAllocationDiagnostics(
            status=status,
            target_total_population=target,
            baseline_population=0,
            generated_population_target=target,
            allocatable_generated_capacity=allocated,
            allocated_generated_population=allocated,
            unmet_generated_population=target - allocated,
            baseline_excess_population=0,
        ),
    )


def test_jobs_follow_explicit_use_density_assumptions() -> None:
    scenario = _scenario()
    result = EmploymentEstimator().estimate(
        (
            _subject("building:res", use=BuildingUse.RESIDENTIAL),
            _subject("building:mixed", use=BuildingUse.MIXED),
            _subject("building:public", use=BuildingUse.PUBLIC),
            _subject("building:commercial", use=BuildingUse.COMMERCIAL),
        ),
        population=_population(scenario),
        scenario=scenario,
        config=_config(),
    )

    by_id = {item.building_id: item for item in result.buildings}
    assert by_id["building:res"].jobs_estimate == 0.0
    assert by_id["building:res"].area_per_job_m2 is None
    assert by_id["building:mixed"].job_supporting_gfa_m2 == 400.0
    assert by_id["building:mixed"].jobs_estimate == 20.0
    assert by_id["building:public"].jobs_estimate == 20.0
    assert by_id["building:commercial"].jobs_estimate == 40.0
    assert result.summary.total_jobs_estimate == 80.0


def test_workforce_uses_actual_allocated_population_not_target() -> None:
    scenario = _scenario(working_ratio=0.6)
    population = _population(scenario, allocated=80, target=100)

    result = EmploymentEstimator().estimate(
        (),
        population=population,
        scenario=scenario,
        config=_config(),
    )

    assert result.summary.generated_population == 80
    assert result.summary.generated_workforce_estimate == 48.0
    assert result.summary.total_jobs_estimate == 0.0


def test_output_is_sorted_and_config_fingerprint_is_canonical() -> None:
    scenario = _scenario()
    first_config = _config()
    second_config = EmploymentConfig(
        version="employment-v1",
        rules=tuple(reversed(first_config.rules)),
    )
    subjects = (
        _subject("building:b", use=BuildingUse.PUBLIC),
        _subject("building:a", use=BuildingUse.COMMERCIAL),
    )

    first = EmploymentEstimator().estimate(
        subjects,
        population=_population(scenario),
        scenario=scenario,
        config=first_config,
    )
    second = EmploymentEstimator().estimate(
        tuple(reversed(subjects)),
        population=_population(scenario),
        scenario=scenario,
        config=second_config,
    )

    assert first == second
    assert [item.building_id for item in first.buildings] == [
        "building:a",
        "building:b",
    ]
    assert first_config.fingerprint == second_config.fingerprint


def test_subject_rejects_metric_attribute_mismatch() -> None:
    metrics = BuildingAreaMetrics(
        building_id="building:metrics",
        footprint_area_m2=100.0,
        floors=2,
        gfa_m2=200.0,
    )
    attributes = _subject(
        "building:attributes",
        use=BuildingUse.COMMERCIAL,
        gfa_m2=200.0,
    ).attributes

    with pytest.raises(EmploymentEstimateError, match="ids must match"):
        EmploymentSubject(metrics=metrics, attributes=attributes)


def test_config_requires_all_job_supporting_uses() -> None:
    with pytest.raises(EmploymentEstimateError, match="must define"):
        EmploymentConfig(
            version="employment-v1",
            rules=(
                JobDensityRule(
                    use=BuildingUse.COMMERCIAL,
                    area_per_job_m2=25.0,
                ),
            ),
        )


def test_residential_job_density_rule_is_rejected() -> None:
    with pytest.raises(EmploymentEstimateError, match="only"):
        JobDensityRule(
            use=BuildingUse.RESIDENTIAL,
            area_per_job_m2=30.0,
        )


def test_estimator_rejects_population_from_different_scenario() -> None:
    first = _scenario()
    second = DemographicScenario(
        version="demography-v2",
        population_target=first.population_target,
        occupancy_ratio=first.occupancy_ratio,
        residential_area_per_person_m2=first.residential_area_per_person_m2,
        average_household_size=first.average_household_size,
        residential_gfa_share=first.residential_gfa_share,
        age_groups=first.age_groups,
        working_population_ratio=first.working_population_ratio,
    )

    with pytest.raises(EmploymentEstimateError, match="version"):
        EmploymentEstimator().estimate(
            (),
            population=_population(first),
            scenario=second,
            config=_config(),
        )


def test_estimator_bounds_subject_count() -> None:
    scenario = _scenario()
    subjects = (
        _subject("building:1", use=BuildingUse.PUBLIC),
        _subject("building:2", use=BuildingUse.PUBLIC),
    )

    with pytest.raises(EmploymentEstimateError, match="limit exceeded"):
        EmploymentEstimator(max_subjects=1).estimate(
            subjects,
            population=_population(scenario),
            scenario=scenario,
            config=_config(),
        )
