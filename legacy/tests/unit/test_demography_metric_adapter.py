import pytest

from core.urban_generator.demography import (
    BlockDemographyMetrics,
    DemographicAgeMetric,
    DemographyMetricsBuilder,
    DemographyMetricsResult,
    DemographyMetricsTotals,
)
from core.urban_generator.domain import (
    CANONICAL_METRIC_REGISTRY,
    MetricSource,
    RawMetricId,
)
from core.urban_generator.metrics import (
    DEMOGRAPHY_RAW_METRIC_IDS,
    DemographyMetricAdapter,
)
from core.urban_generator.stages.demography import DemographyStageOutput
from core.urban_generator.zoning import ZoneClass


def _age_groups(
    *,
    population: int = 100,
) -> tuple[DemographicAgeMetric, ...]:
    child = population // 4
    adult = population - child
    return (
        DemographicAgeMetric(
            code="child",
            min_age=0,
            max_age=17,
            residents=child,
            share=(child / population if population else 0.0),
        ),
        DemographicAgeMetric(
            code="adult",
            min_age=18,
            max_age=None,
            residents=adult,
            share=(adult / population if population else 0.0),
        ),
    )


def _metrics(
    *,
    population: int = 100,
    area_m2: float = 1_000_000.0,
    jobs_estimate: float = 20.0,
) -> DemographyMetricsResult:
    groups = _age_groups(population=population)
    density = (
        population / area_m2 * 1_000_000.0
        if area_m2 > 0.0
        else 0.0
    )
    blocks = (
        (
            BlockDemographyMetrics(
                block_id="block:001",
                zone_id="zone:001",
                zone_class=ZoneClass.RESIDENTIAL,
                area_m2=area_m2,
                population=population,
                population_density_per_km2=density,
                jobs_estimate=jobs_estimate,
                age_groups=groups,
            ),
        )
        if area_m2 > 0.0
        else ()
    )
    return DemographyMetricsResult(
        scenario_version="1",
        scenario_fingerprint="a" * 64,
        employment_config_version="1",
        employment_config_fingerprint="b" * 64,
        blocks=blocks,
        totals=DemographyMetricsTotals(
            block_count=len(blocks),
            area_m2=area_m2,
            population=population,
            population_density_per_km2=density,
            jobs_estimate=jobs_estimate,
            age_groups=groups,
        ),
    )


def _stage_output(
    *,
    metrics: DemographyMetricsResult | None = None,
) -> DemographyStageOutput:
    return DemographyStageOutput(
        capacities=object(),
        population=object(),
        age_groups=object(),
        employment=object(),
        aggregation=object(),
        calibration=None,
        demand_profile=object(),
        metrics=metrics or _metrics(),
        fixed_demography_refs=(),
    )


def _scalar(result, metric_id: RawMetricId) -> float:
    value = result.require(metric_id).scalar_value
    assert value is not None
    return value


def test_adapter_uses_canonical_demography_registry_order() -> None:
    expected = tuple(
        definition.metric_id
        for definition in CANONICAL_METRIC_REGISTRY.definitions_for(
            source=MetricSource.DEMOGRAPHY
        )
    )

    assert DEMOGRAPHY_RAW_METRIC_IDS == expected
    assert DEMOGRAPHY_RAW_METRIC_IDS == (
        RawMetricId.DEMOGRAPHY_TOTAL_POPULATION,
        RawMetricId.DEMOGRAPHY_DENSITY_PER_KM2,
        RawMetricId.DEMOGRAPHY_AGE_GROUP_DISTRIBUTION,
        RawMetricId.DEMOGRAPHY_JOBS_ESTIMATE,
    )


def test_adapter_projects_authoritative_demography_totals() -> None:
    stage_output = _stage_output()
    result = DemographyMetricAdapter().adapt(
        demography=stage_output
    )

    assert tuple(item.metric_id for item in result.raw_metrics) == (
        DEMOGRAPHY_RAW_METRIC_IDS
    )
    assert _scalar(
        result,
        RawMetricId.DEMOGRAPHY_TOTAL_POPULATION,
    ) == 100.0
    assert _scalar(
        result,
        RawMetricId.DEMOGRAPHY_DENSITY_PER_KM2,
    ) == 100.0
    assert _scalar(
        result,
        RawMetricId.DEMOGRAPHY_JOBS_ESTIMATE,
    ) == 20.0

    distribution = result.require(
        RawMetricId.DEMOGRAPHY_AGE_GROUP_DISTRIBUTION
    ).age_distribution
    assert distribution is stage_output.metrics.totals.age_groups
    assert tuple(item.code for item in distribution) == (
        "child",
        "adult",
    )
    assert tuple(item.residents for item in distribution) == (
        25,
        75,
    )
    assert tuple(item.share for item in distribution) == (
        0.25,
        0.75,
    )

    assert result.diagnostics.block_count == 1
    assert result.diagnostics.area_m2 == 1_000_000.0
    assert result.diagnostics.age_group_count == 2
    assert result.diagnostics.scenario_version == "1"
    assert result.diagnostics.employment_config_version == "1"


def test_adapter_preserves_zero_population_distribution_policy() -> None:
    result = DemographyMetricAdapter().adapt(
        demography=_stage_output(
            metrics=_metrics(
                population=0,
                area_m2=1_000_000.0,
                jobs_estimate=0.0,
            )
        )
    )

    assert _scalar(
        result,
        RawMetricId.DEMOGRAPHY_TOTAL_POPULATION,
    ) == 0.0
    assert _scalar(
        result,
        RawMetricId.DEMOGRAPHY_DENSITY_PER_KM2,
    ) == 0.0
    assert _scalar(
        result,
        RawMetricId.DEMOGRAPHY_JOBS_ESTIMATE,
    ) == 0.0

    distribution = result.require(
        RawMetricId.DEMOGRAPHY_AGE_GROUP_DISTRIBUTION
    ).age_distribution
    assert all(item.residents == 0 for item in distribution)
    assert all(item.share == 0.0 for item in distribution)


def test_adapter_does_not_rebuild_demography_metrics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stage_output = _stage_output()

    def _unexpected_build(*args: object, **kwargs: object) -> object:
        raise AssertionError(
            "demography metric rebuild is forbidden in S11-T07"
        )

    monkeypatch.setattr(
        DemographyMetricsBuilder,
        "build",
        _unexpected_build,
    )

    result = DemographyMetricAdapter().adapt(
        demography=stage_output
    )

    assert _scalar(
        result,
        RawMetricId.DEMOGRAPHY_TOTAL_POPULATION,
    ) == 100.0


def test_adapter_requires_typed_demography_stage_output() -> None:
    with pytest.raises(
        ValueError,
        match="DemographyStageOutput",
    ):
        DemographyMetricAdapter().adapt(
            demography=object(),
        )
