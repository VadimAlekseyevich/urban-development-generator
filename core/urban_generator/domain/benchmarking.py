from dataclasses import dataclass
from enum import StrEnum


class BenchmarkContractError(ValueError):
    """Raised when benchmark or experiment metadata violates the core contract."""


class MetricValueKind(StrEnum):
    SCALAR = "SCALAR"
    DISTRIBUTION = "DISTRIBUTION"


class RawMetricId(StrEnum):
    LAND_DEVELOPABLE_AREA_M2 = "land.developable_area_m2"
    LAND_DEVELOPED_AREA_M2 = "land.developed_area_m2"
    LAND_GREEN_RECREATION_SHARE = "land.green_recreation_share"

    BUILDINGS_COVERAGE_RATIO = "buildings.coverage_ratio"
    BUILDINGS_FAR = "buildings.far"
    BUILDINGS_GFA_M2 = "buildings.gfa_m2"
    BUILDINGS_ARCHETYPE_DISTRIBUTION = "buildings.archetype_distribution"

    DEMOGRAPHY_TOTAL_POPULATION = "demography.total_population"
    DEMOGRAPHY_DENSITY_PER_KM2 = "demography.density_per_km2"
    DEMOGRAPHY_AGE_GROUP_DISTRIBUTION = "demography.age_group_distribution"
    DEMOGRAPHY_JOBS_ESTIMATE = "demography.jobs_estimate"

    ROADS_LENGTH_DENSITY_KM_PER_KM2 = "roads.length_density_km_per_km2"
    ROADS_CONNECTED_COMPONENTS = "roads.connected_components"
    ROADS_AVERAGE_DEGREE = "roads.average_degree"
    ROADS_INTERSECTION_DENSITY_PER_KM2 = "roads.intersection_density_per_km2"
    ROADS_CIRCUITY = "roads.circuity"
    ROADS_DEAD_END_RATIO = "roads.dead_end_ratio"

    INFRASTRUCTURE_POPULATION_COVERAGE_RATIO = (
        "infrastructure.population_coverage_ratio"
    )
    INFRASTRUCTURE_AGE_SPECIFIC_COVERAGE = "infrastructure.age_specific_coverage"
    INFRASTRUCTURE_NETWORK_DISTANCE_P50_M = (
        "infrastructure.network_distance_p50_m"
    )
    INFRASTRUCTURE_NETWORK_DISTANCE_P90_M = (
        "infrastructure.network_distance_p90_m"
    )
    INFRASTRUCTURE_UNMET_DEMAND = "infrastructure.unmet_demand"
    INFRASTRUCTURE_CAPACITY_UTILIZATION = "infrastructure.capacity_utilization"

    CONSTRAINTS_HARD_VIOLATION_COUNT = "constraints.hard_violation_count"
    CONSTRAINTS_AFFECTED_AREA_M2 = "constraints.affected_area_m2"
    CONSTRAINTS_WEIGHTED_SOFT_PENALTY = "constraints.weighted_soft_penalty"


class DiagnosticId(StrEnum):
    RUN_DURATION_SECONDS = "run.duration_seconds"
    STAGE_DURATION_SECONDS = "stage.duration_seconds"
    INPUT_FEATURE_COUNT = "stage.input_feature_count"
    OUTPUT_FEATURE_COUNT = "stage.output_feature_count"
    WARNING_COUNT = "stage.warning_count"
    PEAK_MEMORY_BYTES = "stage.peak_memory_bytes"


class ExperimentId(StrEnum):
    E1_REPRODUCIBILITY = "E1"
    E2_SEEDS = "E2"
    E3_DENSITY = "E3"
    E4_CONSTRAINTS_ABLATION = "E4"
    E5_INFRASTRUCTURE_PLACEMENT = "E5"
    E6_SCORE_SENSITIVITY = "E6"
    E7_REAL_GROWTH_HOLDOUT = "E7"


class BaselineId(StrEnum):
    RANDOM_GRID_WITH_HARD_MASKS = "random_grid_with_hard_masks"
    RANDOM_INFRASTRUCTURE = "random_infrastructure"
    CONSTRAINTS_DISABLED_OR_SOFTENED = "constraints_disabled_or_softened"


@dataclass(frozen=True, slots=True)
class MetricDefinition:
    metric_id: RawMetricId
    unit: str
    value_kind: MetricValueKind = MetricValueKind.SCALAR

    def __post_init__(self) -> None:
        if not self.unit.strip():
            raise BenchmarkContractError("metric unit must be non-empty")


@dataclass(frozen=True, slots=True)
class ExperimentDefinition:
    experiment_id: ExperimentId
    description: str
    required_metrics: tuple[RawMetricId, ...]
    required_diagnostics: tuple[DiagnosticId, ...]
    baselines: tuple[BaselineId, ...] = ()
    optional: bool = False

    def __post_init__(self) -> None:
        if not self.description.strip():
            raise BenchmarkContractError("experiment description must be non-empty")
        _require_unique("required_metrics", self.required_metrics)
        _require_unique("required_diagnostics", self.required_diagnostics)
        _require_unique("baselines", self.baselines)


@dataclass(frozen=True, slots=True)
class BenchmarkReferenceProfile:
    profile_id: str
    version: str
    required_metrics: tuple[RawMetricId, ...]
    required_diagnostics: tuple[DiagnosticId, ...]
    required_experiments: tuple[ExperimentId, ...]
    optional_experiments: tuple[ExperimentId, ...] = ()

    def __post_init__(self) -> None:
        if not self.profile_id.strip():
            raise BenchmarkContractError("profile_id must be non-empty")
        if not self.version.strip():
            raise BenchmarkContractError("version must be non-empty")
        _require_unique("required_metrics", self.required_metrics)
        _require_unique("required_diagnostics", self.required_diagnostics)
        _require_unique("required_experiments", self.required_experiments)
        _require_unique("optional_experiments", self.optional_experiments)

        overlap = set(self.required_experiments) & set(self.optional_experiments)
        if overlap:
            raise BenchmarkContractError(
                "required_experiments and optional_experiments must not overlap"
            )


def _require_unique(name: str, values: tuple[object, ...]) -> None:
    if len(values) != len(set(values)):
        raise BenchmarkContractError(f"{name} must not contain duplicates")


CANONICAL_RAW_METRIC_DEFINITIONS = (
    MetricDefinition(RawMetricId.LAND_DEVELOPABLE_AREA_M2, "m2"),
    MetricDefinition(RawMetricId.LAND_DEVELOPED_AREA_M2, "m2"),
    MetricDefinition(RawMetricId.LAND_GREEN_RECREATION_SHARE, "ratio"),
    MetricDefinition(RawMetricId.BUILDINGS_COVERAGE_RATIO, "ratio"),
    MetricDefinition(RawMetricId.BUILDINGS_FAR, "ratio"),
    MetricDefinition(RawMetricId.BUILDINGS_GFA_M2, "m2"),
    MetricDefinition(
        RawMetricId.BUILDINGS_ARCHETYPE_DISTRIBUTION,
        "share",
        MetricValueKind.DISTRIBUTION,
    ),
    MetricDefinition(RawMetricId.DEMOGRAPHY_TOTAL_POPULATION, "persons"),
    MetricDefinition(
        RawMetricId.DEMOGRAPHY_DENSITY_PER_KM2,
        "persons_per_km2",
    ),
    MetricDefinition(
        RawMetricId.DEMOGRAPHY_AGE_GROUP_DISTRIBUTION,
        "share",
        MetricValueKind.DISTRIBUTION,
    ),
    MetricDefinition(RawMetricId.DEMOGRAPHY_JOBS_ESTIMATE, "jobs"),
    MetricDefinition(
        RawMetricId.ROADS_LENGTH_DENSITY_KM_PER_KM2,
        "km_per_km2",
    ),
    MetricDefinition(RawMetricId.ROADS_CONNECTED_COMPONENTS, "count"),
    MetricDefinition(RawMetricId.ROADS_AVERAGE_DEGREE, "degree"),
    MetricDefinition(
        RawMetricId.ROADS_INTERSECTION_DENSITY_PER_KM2,
        "count_per_km2",
    ),
    MetricDefinition(RawMetricId.ROADS_CIRCUITY, "ratio"),
    MetricDefinition(RawMetricId.ROADS_DEAD_END_RATIO, "ratio"),
    MetricDefinition(
        RawMetricId.INFRASTRUCTURE_POPULATION_COVERAGE_RATIO,
        "ratio",
    ),
    MetricDefinition(
        RawMetricId.INFRASTRUCTURE_AGE_SPECIFIC_COVERAGE,
        "ratio",
        MetricValueKind.DISTRIBUTION,
    ),
    MetricDefinition(
        RawMetricId.INFRASTRUCTURE_NETWORK_DISTANCE_P50_M,
        "m",
    ),
    MetricDefinition(
        RawMetricId.INFRASTRUCTURE_NETWORK_DISTANCE_P90_M,
        "m",
    ),
    MetricDefinition(RawMetricId.INFRASTRUCTURE_UNMET_DEMAND, "demand_units"),
    MetricDefinition(
        RawMetricId.INFRASTRUCTURE_CAPACITY_UTILIZATION,
        "ratio",
    ),
    MetricDefinition(RawMetricId.CONSTRAINTS_HARD_VIOLATION_COUNT, "count"),
    MetricDefinition(RawMetricId.CONSTRAINTS_AFFECTED_AREA_M2, "m2"),
    MetricDefinition(RawMetricId.CONSTRAINTS_WEIGHTED_SOFT_PENALTY, "score"),
)

CANONICAL_RAW_METRIC_IDS = tuple(
    definition.metric_id for definition in CANONICAL_RAW_METRIC_DEFINITIONS
)

CANONICAL_DIAGNOSTIC_IDS = (
    DiagnosticId.RUN_DURATION_SECONDS,
    DiagnosticId.STAGE_DURATION_SECONDS,
    DiagnosticId.INPUT_FEATURE_COUNT,
    DiagnosticId.OUTPUT_FEATURE_COUNT,
    DiagnosticId.WARNING_COUNT,
    DiagnosticId.PEAK_MEMORY_BYTES,
)

_INFRASTRUCTURE_METRICS = (
    RawMetricId.INFRASTRUCTURE_POPULATION_COVERAGE_RATIO,
    RawMetricId.INFRASTRUCTURE_AGE_SPECIFIC_COVERAGE,
    RawMetricId.INFRASTRUCTURE_NETWORK_DISTANCE_P50_M,
    RawMetricId.INFRASTRUCTURE_NETWORK_DISTANCE_P90_M,
    RawMetricId.INFRASTRUCTURE_UNMET_DEMAND,
    RawMetricId.INFRASTRUCTURE_CAPACITY_UTILIZATION,
)

_CONSTRAINT_METRICS = (
    RawMetricId.CONSTRAINTS_HARD_VIOLATION_COUNT,
    RawMetricId.CONSTRAINTS_AFFECTED_AREA_M2,
    RawMetricId.CONSTRAINTS_WEIGHTED_SOFT_PENALTY,
)

V1_EXPERIMENT_DEFINITIONS = (
    ExperimentDefinition(
        experiment_id=ExperimentId.E1_REPRODUCIBILITY,
        description="Same seed/config must reproduce the same evaluation outputs.",
        required_metrics=CANONICAL_RAW_METRIC_IDS,
        required_diagnostics=CANONICAL_DIAGNOSTIC_IDS,
    ),
    ExperimentDefinition(
        experiment_id=ExperimentId.E2_SEEDS,
        description="Compare morphology and accessibility across a series of seeds.",
        required_metrics=CANONICAL_RAW_METRIC_IDS,
        required_diagnostics=CANONICAL_DIAGNOSTIC_IDS,
    ),
    ExperimentDefinition(
        experiment_id=ExperimentId.E3_DENSITY,
        description="Compare low, medium and high density configurations.",
        required_metrics=CANONICAL_RAW_METRIC_IDS,
        required_diagnostics=CANONICAL_DIAGNOSTIC_IDS,
    ),
    ExperimentDefinition(
        experiment_id=ExperimentId.E4_CONSTRAINTS_ABLATION,
        description="Compare constraint-aware generation with a simplified baseline.",
        required_metrics=_CONSTRAINT_METRICS,
        required_diagnostics=CANONICAL_DIAGNOSTIC_IDS,
        baselines=(BaselineId.CONSTRAINTS_DISABLED_OR_SOFTENED,),
    ),
    ExperimentDefinition(
        experiment_id=ExperimentId.E5_INFRASTRUCTURE_PLACEMENT,
        description=(
            "Compare network-aware greedy placement with random or naive placement."
        ),
        required_metrics=_INFRASTRUCTURE_METRICS,
        required_diagnostics=CANONICAL_DIAGNOSTIC_IDS,
        baselines=(BaselineId.RANDOM_INFRASTRUCTURE,),
    ),
    ExperimentDefinition(
        experiment_id=ExperimentId.E6_SCORE_SENSITIVITY,
        description="Evaluate sensitivity to composite-score weight changes.",
        required_metrics=CANONICAL_RAW_METRIC_IDS,
        required_diagnostics=CANONICAL_DIAGNOSTIC_IDS,
    ),
    ExperimentDefinition(
        experiment_id=ExperimentId.E7_REAL_GROWTH_HOLDOUT,
        description="Optional holdout comparison against real temporal growth data.",
        required_metrics=CANONICAL_RAW_METRIC_IDS,
        required_diagnostics=CANONICAL_DIAGNOSTIC_IDS,
        optional=True,
    ),
)

V1_REFERENCE_PROFILE = BenchmarkReferenceProfile(
    profile_id="v1.research",
    version="1",
    required_metrics=CANONICAL_RAW_METRIC_IDS,
    required_diagnostics=CANONICAL_DIAGNOSTIC_IDS,
    required_experiments=(
        ExperimentId.E1_REPRODUCIBILITY,
        ExperimentId.E2_SEEDS,
        ExperimentId.E3_DENSITY,
        ExperimentId.E4_CONSTRAINTS_ABLATION,
        ExperimentId.E5_INFRASTRUCTURE_PLACEMENT,
        ExperimentId.E6_SCORE_SENSITIVITY,
    ),
    optional_experiments=(ExperimentId.E7_REAL_GROWTH_HOLDOUT,),
)
