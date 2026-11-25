from __future__ import annotations

import math
from dataclasses import dataclass

from core.urban_generator.demography import DemographicAgeMetric
from core.urban_generator.domain import (
    CANONICAL_METRIC_REGISTRY,
    MetricSource,
    RawMetricId,
)
from core.urban_generator.stages.demography import DemographyStageOutput


class DemographyMetricAdapterError(ValueError):
    """Raised when S11-T07 demography metric artifacts violate the adapter contract."""


DEMOGRAPHY_RAW_METRIC_IDS = tuple(
    definition.metric_id
    for definition in CANONICAL_METRIC_REGISTRY.definitions_for(
        source=MetricSource.DEMOGRAPHY
    )
)


@dataclass(frozen=True, slots=True)
class DemographyRawMetricValue:
    """One canonical demography raw metric projected from S09 metrics."""

    metric_id: RawMetricId
    scalar_value: float | None = None
    age_distribution: tuple[DemographicAgeMetric, ...] = ()

    def __post_init__(self) -> None:
        if self.metric_id not in DEMOGRAPHY_RAW_METRIC_IDS:
            raise DemographyMetricAdapterError(
                f"not a demography RawMetricId: {self.metric_id!r}"
            )

        if self.metric_id is RawMetricId.DEMOGRAPHY_AGE_GROUP_DISTRIBUTION:
            if self.scalar_value is not None:
                raise DemographyMetricAdapterError(
                    "age-group distribution must not carry scalar_value"
                )
            if not isinstance(self.age_distribution, tuple):
                raise DemographyMetricAdapterError(
                    "age_distribution must be an immutable tuple"
                )
            if not self.age_distribution:
                raise DemographyMetricAdapterError(
                    "age_distribution must not be empty"
                )
            if any(
                not isinstance(item, DemographicAgeMetric)
                for item in self.age_distribution
            ):
                raise DemographyMetricAdapterError(
                    "age_distribution must contain DemographicAgeMetric values"
                )
            codes = tuple(item.code for item in self.age_distribution)
            if len(codes) != len(set(codes)):
                raise DemographyMetricAdapterError(
                    "age_distribution codes must be unique"
                )
            return

        if self.age_distribution:
            raise DemographyMetricAdapterError(
                "scalar demography metric must not carry age_distribution"
            )
        if self.scalar_value is None:
            raise DemographyMetricAdapterError(
                "scalar demography metric requires scalar_value"
            )
        scalar = _require_non_negative_finite(
            "demography scalar metric",
            self.scalar_value,
        )
        object.__setattr__(self, "scalar_value", scalar)


@dataclass(frozen=True, slots=True)
class DemographyMetricAdapterDiagnostics:
    block_count: int
    area_m2: float
    age_group_count: int
    scenario_version: str
    employment_config_version: str
    fixed_demography_ref_count: int

    def __post_init__(self) -> None:
        for field_name in (
            "block_count",
            "age_group_count",
            "fixed_demography_ref_count",
        ):
            value = getattr(self, field_name)
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 0
            ):
                raise DemographyMetricAdapterError(
                    f"{field_name} must be a non-negative integer"
                )
        _require_non_negative_finite("area_m2", self.area_m2)
        for field_name in (
            "scenario_version",
            "employment_config_version",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise DemographyMetricAdapterError(
                    f"{field_name} must be a non-empty string"
                )


@dataclass(frozen=True, slots=True)
class DemographyRawMetricsResult:
    raw_metrics: tuple[DemographyRawMetricValue, ...]
    diagnostics: DemographyMetricAdapterDiagnostics

    def __post_init__(self) -> None:
        if not isinstance(self.raw_metrics, tuple):
            raise DemographyMetricAdapterError(
                "raw_metrics must be an immutable tuple"
            )
        if any(
            not isinstance(item, DemographyRawMetricValue)
            for item in self.raw_metrics
        ):
            raise DemographyMetricAdapterError(
                "raw_metrics must contain DemographyRawMetricValue values"
            )
        actual_ids = tuple(item.metric_id for item in self.raw_metrics)
        if actual_ids != DEMOGRAPHY_RAW_METRIC_IDS:
            raise DemographyMetricAdapterError(
                "raw_metrics must contain every canonical demography metric "
                "in registry order"
            )
        if not isinstance(
            self.diagnostics,
            DemographyMetricAdapterDiagnostics,
        ):
            raise DemographyMetricAdapterError(
                "diagnostics must be DemographyMetricAdapterDiagnostics"
            )

    def require(self, metric_id: RawMetricId) -> DemographyRawMetricValue:
        if metric_id not in DEMOGRAPHY_RAW_METRIC_IDS:
            raise DemographyMetricAdapterError(
                f"not a demography RawMetricId: {metric_id!r}"
            )
        return self.raw_metrics[
            DEMOGRAPHY_RAW_METRIC_IDS.index(metric_id)
        ]


class DemographyMetricAdapter:
    """Project authoritative S09 demographic totals into canonical raw metrics."""

    version = "1"

    def adapt(
        self,
        *,
        demography: DemographyStageOutput,
    ) -> DemographyRawMetricsResult:
        if not isinstance(demography, DemographyStageOutput):
            raise DemographyMetricAdapterError(
                "demography must be DemographyStageOutput"
            )

        metrics = demography.metrics
        totals = metrics.totals
        self._validate_totals(demography=demography)

        values_by_id = {
            RawMetricId.DEMOGRAPHY_TOTAL_POPULATION: (
                DemographyRawMetricValue(
                    metric_id=RawMetricId.DEMOGRAPHY_TOTAL_POPULATION,
                    scalar_value=float(totals.population),
                )
            ),
            RawMetricId.DEMOGRAPHY_DENSITY_PER_KM2: (
                DemographyRawMetricValue(
                    metric_id=RawMetricId.DEMOGRAPHY_DENSITY_PER_KM2,
                    scalar_value=totals.population_density_per_km2,
                )
            ),
            RawMetricId.DEMOGRAPHY_AGE_GROUP_DISTRIBUTION: (
                DemographyRawMetricValue(
                    metric_id=(
                        RawMetricId.DEMOGRAPHY_AGE_GROUP_DISTRIBUTION
                    ),
                    age_distribution=totals.age_groups,
                )
            ),
            RawMetricId.DEMOGRAPHY_JOBS_ESTIMATE: (
                DemographyRawMetricValue(
                    metric_id=RawMetricId.DEMOGRAPHY_JOBS_ESTIMATE,
                    scalar_value=totals.jobs_estimate,
                )
            ),
        }

        return DemographyRawMetricsResult(
            raw_metrics=tuple(
                values_by_id[metric_id]
                for metric_id in DEMOGRAPHY_RAW_METRIC_IDS
            ),
            diagnostics=DemographyMetricAdapterDiagnostics(
                block_count=totals.block_count,
                area_m2=totals.area_m2,
                age_group_count=len(totals.age_groups),
                scenario_version=metrics.scenario_version,
                employment_config_version=(
                    metrics.employment_config_version
                ),
                fixed_demography_ref_count=len(
                    demography.fixed_demography_refs
                ),
            ),
        )

    @staticmethod
    def _validate_totals(
        *,
        demography: DemographyStageOutput,
    ) -> None:
        metrics = demography.metrics
        totals = metrics.totals

        if totals.block_count != len(metrics.blocks):
            raise DemographyMetricAdapterError(
                "demography metric block_count must match metric blocks"
            )
        if sum(item.population for item in metrics.blocks) != totals.population:
            raise DemographyMetricAdapterError(
                "demography block population must match metric total"
            )
        if not math.isclose(
            math.fsum(item.jobs_estimate for item in metrics.blocks),
            totals.jobs_estimate,
            rel_tol=1e-12,
            abs_tol=1e-9,
        ):
            raise DemographyMetricAdapterError(
                "demography block jobs must match metric total"
            )

        expected_share_sum = 1.0 if totals.population > 0 else 0.0
        if not math.isclose(
            math.fsum(item.share for item in totals.age_groups),
            expected_share_sum,
            rel_tol=0.0,
            abs_tol=1e-9,
        ):
            raise DemographyMetricAdapterError(
                "demography age-group shares must match population policy"
            )
        if (
            sum(item.residents for item in totals.age_groups)
            != totals.population
        ):
            raise DemographyMetricAdapterError(
                "demography age-group residents must match total population"
            )


def _require_non_negative_finite(
    field_name: str,
    value: float | int,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DemographyMetricAdapterError(
            f"{field_name} must be a finite non-negative number"
        )
    numeric = float(value)
    if not math.isfinite(numeric) or numeric < 0.0:
        raise DemographyMetricAdapterError(
            f"{field_name} must be a finite non-negative number"
        )
    return numeric
