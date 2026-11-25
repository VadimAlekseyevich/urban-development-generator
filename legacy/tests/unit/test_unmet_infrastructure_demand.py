from __future__ import annotations

import pytest
from shapely.geometry import Point

from core.urban_generator.demography import (
    BlockDemographicDemand,
    DemographicDemandCategory,
    DemographicDemandProfile,
    DemographicDemandSignal,
    DemographicDemandTotals,
    DemographicDemandUnit,
)
from core.urban_generator.infrastructure import (
    ExistingInfrastructureDiagnostics,
    ExistingInfrastructureFacility,
    ExistingInfrastructureResult,
    InfrastructureCandidatePolicy,
    InfrastructureCandidateSource,
    InfrastructureCategory,
    InfrastructureDemandModel,
    InfrastructureServedDemand,
    InfrastructureType,
    UnmetDemandCalculator,
    UnmetDemandError,
)
from core.urban_generator.zoning import ZoneClass


def _signals(
    *,
    population: float,
    child: float,
    adult: float,
    workforce: float,
    jobs: float,
) -> tuple[DemographicDemandSignal, ...]:
    return (
        DemographicDemandSignal(
            category=DemographicDemandCategory.POPULATION,
            value=population,
            unit=DemographicDemandUnit.PEOPLE,
        ),
        DemographicDemandSignal(
            category=DemographicDemandCategory.AGE_GROUP,
            value=child,
            unit=DemographicDemandUnit.PEOPLE,
            demographic_group="child",
            min_age=0,
            max_age=17,
        ),
        DemographicDemandSignal(
            category=DemographicDemandCategory.AGE_GROUP,
            value=adult,
            unit=DemographicDemandUnit.PEOPLE,
            demographic_group="adult",
            min_age=18,
            max_age=None,
        ),
        DemographicDemandSignal(
            category=DemographicDemandCategory.WORKFORCE,
            value=workforce,
            unit=DemographicDemandUnit.PEOPLE,
        ),
        DemographicDemandSignal(
            category=DemographicDemandCategory.JOBS,
            value=jobs,
            unit=DemographicDemandUnit.JOBS,
        ),
    )


def _profile() -> DemographicDemandProfile:
    return DemographicDemandProfile(
        scenario_version="demography-v1",
        scenario_fingerprint="a" * 64,
        blocks=(
            BlockDemographicDemand(
                block_id="block-1",
                zone_id="zone-1",
                zone_class=ZoneClass.RESIDENTIAL,
                signals=_signals(
                    population=100.0,
                    child=20.0,
                    adult=80.0,
                    workforce=60.0,
                    jobs=10.0,
                ),
            ),
            BlockDemographicDemand(
                block_id="block-2",
                zone_id="zone-1",
                zone_class=ZoneClass.MIXED,
                signals=_signals(
                    population=50.0,
                    child=10.0,
                    adult=40.0,
                    workforce=30.0,
                    jobs=20.0,
                ),
            ),
        ),
        totals=DemographicDemandTotals(
            block_count=2,
            signals=_signals(
                population=150.0,
                child=30.0,
                adult=120.0,
                workforce=90.0,
                jobs=30.0,
            ),
        ),
    )


def _type(
    code: str,
    *,
    category: InfrastructureCategory,
    signal: DemographicDemandCategory,
    rate: float,
    demographic_group: str | None = None,
    capacity: float = 100.0,
) -> InfrastructureType:
    return InfrastructureType(
        version="infrastructure-v1",
        code=code,
        category=category,
        demand_model=InfrastructureDemandModel(
            signal=signal,
            demand_rate=rate,
            demographic_group=demographic_group,
        ),
        capacity=capacity,
        max_network_distance_m=1500.0,
        allowed_zones=(ZoneClass.PUBLIC, ZoneClass.MIXED, ZoneClass.RESIDENTIAL),
        minimum_site_area_m2=100.0,
        target_site_area_m2=200.0,
        candidate_policy=InfrastructureCandidatePolicy(
            sources=(InfrastructureCandidateSource.PARCEL,)
        ),
    )


def _school() -> InfrastructureType:
    return _type(
        "school.general",
        category=InfrastructureCategory.EDUCATION,
        signal=DemographicDemandCategory.AGE_GROUP,
        demographic_group="child",
        rate=0.8,
        capacity=200.0,
    )


def _retail() -> InfrastructureType:
    return _type(
        "retail.local",
        category=InfrastructureCategory.RETAIL,
        signal=DemographicDemandCategory.POPULATION,
        rate=0.05,
        capacity=50.0,
    )


def _existing() -> ExistingInfrastructureResult:
    return ExistingInfrastructureResult(
        snapshot_id="11111111-1111-1111-1111-111111111111",
        facilities=(
            ExistingInfrastructureFacility(
                facility_id="dataset:facilities:v1:school-1",
                source_ref="dataset:facilities:v1",
                source_feature_id="school-1",
                infrastructure_type_code="school.general",
                capacity=120.0,
                geometry=Point(0, 0),
                working_srid=3857,
            ),
            ExistingInfrastructureFacility(
                facility_id="dataset:facilities:v1:school-2",
                source_ref="dataset:facilities:v1",
                source_feature_id="school-2",
                infrastructure_type_code="school.general",
                capacity=80.0,
                geometry=Point(10, 10),
                working_srid=3857,
            ),
        ),
        diagnostics=ExistingInfrastructureDiagnostics(
            input_count=2,
            mapped_count=2,
            skipped_unmapped_count=0,
            source_layer_count=1,
            infrastructure_type_count=1,
        ),
    )


def test_calculator_builds_block_type_demand_from_s09_signals() -> None:
    result = UnmetDemandCalculator().calculate(
        _profile(),
        infrastructure_types=(_retail(), _school()),
    )

    assert [
        (item.block_id, item.infrastructure_type_code)
        for item in result.demands
    ] == [
        ("block-1", "retail.local"),
        ("block-1", "school.general"),
        ("block-2", "retail.local"),
        ("block-2", "school.general"),
    ]

    by_key = {item.key: item for item in result.demands}
    school_1 = by_key[("block-1", "school.general")]
    school_2 = by_key[("block-2", "school.general")]
    retail_1 = by_key[("block-1", "retail.local")]

    assert school_1.demographic_signal is DemographicDemandCategory.AGE_GROUP
    assert school_1.demographic_group == "child"
    assert school_1.source_signal_value == pytest.approx(20.0)
    assert school_1.gross_demand == pytest.approx(16.0)
    assert school_2.gross_demand == pytest.approx(8.0)
    assert retail_1.gross_demand == pytest.approx(5.0)
    assert all(item.served_demand == 0.0 for item in result.demands)
    assert all(
        item.unmet_demand == pytest.approx(item.gross_demand)
        for item in result.demands
    )


def test_served_assignments_reduce_unmet_for_exact_block_type_pair() -> None:
    result = UnmetDemandCalculator().calculate(
        _profile(),
        infrastructure_types=(_school(),),
        served=(
            InfrastructureServedDemand(
                block_id="block-1",
                infrastructure_type_code="school.general",
                value=6.0,
            ),
        ),
    )

    first, second = result.demands
    assert first.gross_demand == pytest.approx(16.0)
    assert first.served_demand == pytest.approx(6.0)
    assert first.unmet_demand == pytest.approx(10.0)
    assert second.gross_demand == pytest.approx(8.0)
    assert second.served_demand == 0.0
    assert second.unmet_demand == pytest.approx(8.0)

    summary = result.summaries[0]
    assert summary.gross_demand == pytest.approx(24.0)
    assert summary.served_demand == pytest.approx(6.0)
    assert summary.unmet_demand == pytest.approx(18.0)


def test_existing_capacity_is_reported_but_not_assumed_spatially_served() -> None:
    result = UnmetDemandCalculator().calculate(
        _profile(),
        infrastructure_types=(_school(),),
        existing=_existing(),
    )

    summary = result.summaries[0]
    assert summary.existing_capacity == pytest.approx(200.0)
    assert summary.gross_demand == pytest.approx(24.0)
    assert summary.served_demand == 0.0
    assert summary.unmet_demand == pytest.approx(24.0)
    assert result.diagnostics.existing_facility_count == 2


def test_served_demand_cannot_exceed_gross_block_demand() -> None:
    with pytest.raises(UnmetDemandError, match="exceeds gross demand"):
        UnmetDemandCalculator().calculate(
            _profile(),
            infrastructure_types=(_school(),),
            served=(
                InfrastructureServedDemand(
                    block_id="block-1",
                    infrastructure_type_code="school.general",
                    value=17.0,
                ),
            ),
        )


def test_unknown_served_pair_is_rejected() -> None:
    with pytest.raises(UnmetDemandError, match="unknown block/type pair"):
        UnmetDemandCalculator().calculate(
            _profile(),
            infrastructure_types=(_school(),),
            served=(
                InfrastructureServedDemand(
                    block_id="block-missing",
                    infrastructure_type_code="school.general",
                    value=1.0,
                ),
            ),
        )


def test_duplicate_served_pair_is_rejected() -> None:
    item = InfrastructureServedDemand(
        block_id="block-1",
        infrastructure_type_code="school.general",
        value=1.0,
    )

    with pytest.raises(UnmetDemandError, match="duplicate served demand"):
        UnmetDemandCalculator().calculate(
            _profile(),
            infrastructure_types=(_school(),),
            served=(item, item),
        )


def test_existing_facility_type_must_participate_in_calculation() -> None:
    with pytest.raises(UnmetDemandError, match="outside demand calculation"):
        UnmetDemandCalculator().calculate(
            _profile(),
            infrastructure_types=(_retail(),),
            existing=_existing(),
        )


def test_duplicate_infrastructure_type_code_is_rejected() -> None:
    school = _school()

    with pytest.raises(UnmetDemandError, match="duplicate infrastructure type"):
        UnmetDemandCalculator().calculate(
            _profile(),
            infrastructure_types=(school, school),
        )


def test_work_item_limit_bounds_blocks_times_types() -> None:
    with pytest.raises(UnmetDemandError, match="work item limit exceeded"):
        UnmetDemandCalculator(max_demand_items=3).calculate(
            _profile(),
            infrastructure_types=(_school(), _retail()),
        )


def test_result_is_deterministic_for_type_input_order() -> None:
    calculator = UnmetDemandCalculator()

    first = calculator.calculate(
        _profile(),
        infrastructure_types=(_school(), _retail()),
    )
    second = calculator.calculate(
        _profile(),
        infrastructure_types=(_retail(), _school()),
    )

    assert first == second
