import re
from dataclasses import dataclass
from enum import StrEnum


class BenchmarkContractError(ValueError):
    """Raised when benchmark or experiment metadata violates the core contract."""


class MetricValueKind(StrEnum):
    SCALAR = "SCALAR"
    DISTRIBUTION = "DISTRIBUTION"


class MetricScope(StrEnum):
    """Runtime domain grouping for canonical raw metrics."""

    LAND = "LAND"
    BUILDINGS = "BUILDINGS"
    ROADS = "ROADS"
    DEMOGRAPHY = "DEMOGRAPHY"
    INFRASTRUCTURE = "INFRASTRUCTURE"
    CONSTRAINTS = "CONSTRAINTS"


class MetricDirection(StrEnum):
    """How a scalar metric should be interpreted before later normalization."""

    HIGHER_IS_BETTER = "HIGHER_IS_BETTER"
    LOWER_IS_BETTER = "LOWER_IS_BETTER"
    TARGET = "TARGET"
    DESCRIPTIVE = "DESCRIPTIVE"


class MetricSource(StrEnum):
    """Canonical producer-family ownership for raw metric values."""

    LAND_BUILDING = "LAND_BUILDING"
    ROADS = "ROADS"
    DEMOGRAPHY = "DEMOGRAPHY"
    INFRASTRUCTURE = "INFRASTRUCTURE"
    CONSTRAINTS = "CONSTRAINTS"


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
    scope: MetricScope
    direction: MetricDirection
    source: MetricSource
    version: str
    value_kind: MetricValueKind = MetricValueKind.SCALAR

    def __post_init__(self) -> None:
        if not isinstance(self.metric_id, RawMetricId):
            raise BenchmarkContractError("metric_id must be a RawMetricId value")
        if not isinstance(self.unit, str) or not self.unit.strip():
            raise BenchmarkContractError("metric unit must be non-empty")
        if not isinstance(self.scope, MetricScope):
            raise BenchmarkContractError("metric scope must be a MetricScope value")
        if not isinstance(self.direction, MetricDirection):
            raise BenchmarkContractError(
                "metric direction must be a MetricDirection value"
            )
        if not isinstance(self.source, MetricSource):
            raise BenchmarkContractError("metric source must be a MetricSource value")
        if (
            not isinstance(self.version, str)
            or _METRIC_VERSION_RE.fullmatch(self.version) is None
        ):
            raise BenchmarkContractError(
                "metric version must be a stable non-empty identifier"
            )
        if not isinstance(self.value_kind, MetricValueKind):
            raise BenchmarkContractError(
                "metric value_kind must be a MetricValueKind value"
            )


class MetricRegistry:
    """Deterministic immutable registry keyed only by canonical RawMetricId."""

    def __init__(self, definitions: tuple[MetricDefinition, ...]) -> None:
        if not isinstance(definitions, tuple):
            raise BenchmarkContractError(
                "metric registry definitions must be an immutable tuple"
            )
        if not definitions:
            raise BenchmarkContractError(
                "metric registry definitions must not be empty"
            )
        if any(not isinstance(item, MetricDefinition) for item in definitions):
            raise BenchmarkContractError(
                "metric registry definitions must contain only MetricDefinition values"
            )

        metric_ids = tuple(item.metric_id for item in definitions)
        if len(metric_ids) != len(set(metric_ids)):
            raise BenchmarkContractError(
                "metric registry definitions must not contain duplicate metric IDs"
            )

        self._definitions = definitions
        self._by_id = {definition.metric_id: definition for definition in definitions}

    @property
    def definitions(self) -> tuple[MetricDefinition, ...]:
        return self._definitions

    @property
    def metric_ids(self) -> tuple[RawMetricId, ...]:
        return tuple(definition.metric_id for definition in self._definitions)

    def get(self, metric_id: RawMetricId) -> MetricDefinition:
        if not isinstance(metric_id, RawMetricId):
            raise BenchmarkContractError(
                "metric registry lookup requires a RawMetricId value"
            )
        try:
            return self._by_id[metric_id]
        except KeyError as exc:
            raise BenchmarkContractError(
                f"metric definition is not registered: {metric_id.value}"
            ) from exc

    def definitions_for(
        self,
        *,
        scope: MetricScope | None = None,
        source: MetricSource | None = None,
    ) -> tuple[MetricDefinition, ...]:
        if scope is not None and not isinstance(scope, MetricScope):
            raise BenchmarkContractError(
                "metric registry scope filter must be MetricScope or None"
            )
        if source is not None and not isinstance(source, MetricSource):
            raise BenchmarkContractError(
                "metric registry source filter must be MetricSource or None"
            )
        return tuple(
            definition
            for definition in self._definitions
            if (scope is None or definition.scope is scope)
            and (source is None or definition.source is source)
        )


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


_METRIC_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


CANONICAL_RAW_METRIC_DEFINITIONS = (
    MetricDefinition(
        metric_id=RawMetricId.LAND_DEVELOPABLE_AREA_M2,
        unit="m2",
        scope=MetricScope.LAND,
        direction=MetricDirection.DESCRIPTIVE,
        source=MetricSource.LAND_BUILDING,
        version="1",
    ),
    MetricDefinition(
        metric_id=RawMetricId.LAND_DEVELOPED_AREA_M2,
        unit="m2",
        scope=MetricScope.LAND,
        direction=MetricDirection.TARGET,
        source=MetricSource.LAND_BUILDING,
        version="1",
    ),
    MetricDefinition(
        metric_id=RawMetricId.LAND_GREEN_RECREATION_SHARE,
        unit="ratio",
        scope=MetricScope.LAND,
        direction=MetricDirection.TARGET,
        source=MetricSource.LAND_BUILDING,
        version="1",
    ),
    MetricDefinition(
        metric_id=RawMetricId.BUILDINGS_COVERAGE_RATIO,
        unit="ratio",
        scope=MetricScope.BUILDINGS,
        direction=MetricDirection.TARGET,
        source=MetricSource.LAND_BUILDING,
        version="1",
    ),
    MetricDefinition(
        metric_id=RawMetricId.BUILDINGS_FAR,
        unit="ratio",
        scope=MetricScope.BUILDINGS,
        direction=MetricDirection.TARGET,
        source=MetricSource.LAND_BUILDING,
        version="1",
    ),
    MetricDefinition(
        metric_id=RawMetricId.BUILDINGS_GFA_M2,
        unit="m2",
        scope=MetricScope.BUILDINGS,
        direction=MetricDirection.TARGET,
        source=MetricSource.LAND_BUILDING,
        version="1",
    ),
    MetricDefinition(
        metric_id=RawMetricId.BUILDINGS_ARCHETYPE_DISTRIBUTION,
        unit="share",
        scope=MetricScope.BUILDINGS,
        direction=MetricDirection.DESCRIPTIVE,
        source=MetricSource.LAND_BUILDING,
        version="1",
        value_kind=MetricValueKind.DISTRIBUTION,
    ),
    MetricDefinition(
        metric_id=RawMetricId.DEMOGRAPHY_TOTAL_POPULATION,
        unit="persons",
        scope=MetricScope.DEMOGRAPHY,
        direction=MetricDirection.TARGET,
        source=MetricSource.DEMOGRAPHY,
        version="1",
    ),
    MetricDefinition(
        metric_id=RawMetricId.DEMOGRAPHY_DENSITY_PER_KM2,
        unit="persons_per_km2",
        scope=MetricScope.DEMOGRAPHY,
        direction=MetricDirection.TARGET,
        source=MetricSource.DEMOGRAPHY,
        version="1",
    ),
    MetricDefinition(
        metric_id=RawMetricId.DEMOGRAPHY_AGE_GROUP_DISTRIBUTION,
        unit="share",
        scope=MetricScope.DEMOGRAPHY,
        direction=MetricDirection.DESCRIPTIVE,
        source=MetricSource.DEMOGRAPHY,
        version="1",
        value_kind=MetricValueKind.DISTRIBUTION,
    ),
    MetricDefinition(
        metric_id=RawMetricId.DEMOGRAPHY_JOBS_ESTIMATE,
        unit="jobs",
        scope=MetricScope.DEMOGRAPHY,
        direction=MetricDirection.TARGET,
        source=MetricSource.DEMOGRAPHY,
        version="1",
    ),
    MetricDefinition(
        metric_id=RawMetricId.ROADS_LENGTH_DENSITY_KM_PER_KM2,
        unit="km_per_km2",
        scope=MetricScope.ROADS,
        direction=MetricDirection.TARGET,
        source=MetricSource.ROADS,
        version="1",
    ),
    MetricDefinition(
        metric_id=RawMetricId.ROADS_CONNECTED_COMPONENTS,
        unit="count",
        scope=MetricScope.ROADS,
        direction=MetricDirection.LOWER_IS_BETTER,
        source=MetricSource.ROADS,
        version="1",
    ),
    MetricDefinition(
        metric_id=RawMetricId.ROADS_AVERAGE_DEGREE,
        unit="degree",
        scope=MetricScope.ROADS,
        direction=MetricDirection.TARGET,
        source=MetricSource.ROADS,
        version="1",
    ),
    MetricDefinition(
        metric_id=RawMetricId.ROADS_INTERSECTION_DENSITY_PER_KM2,
        unit="count_per_km2",
        scope=MetricScope.ROADS,
        direction=MetricDirection.TARGET,
        source=MetricSource.ROADS,
        version="1",
    ),
    MetricDefinition(
        metric_id=RawMetricId.ROADS_CIRCUITY,
        unit="ratio",
        scope=MetricScope.ROADS,
        direction=MetricDirection.LOWER_IS_BETTER,
        source=MetricSource.ROADS,
        version="1",
    ),
    MetricDefinition(
        metric_id=RawMetricId.ROADS_DEAD_END_RATIO,
        unit="ratio",
        scope=MetricScope.ROADS,
        direction=MetricDirection.LOWER_IS_BETTER,
        source=MetricSource.ROADS,
        version="1",
    ),
    MetricDefinition(
        metric_id=RawMetricId.INFRASTRUCTURE_POPULATION_COVERAGE_RATIO,
        unit="ratio",
        scope=MetricScope.INFRASTRUCTURE,
        direction=MetricDirection.HIGHER_IS_BETTER,
        source=MetricSource.INFRASTRUCTURE,
        version="1",
    ),
    MetricDefinition(
        metric_id=RawMetricId.INFRASTRUCTURE_AGE_SPECIFIC_COVERAGE,
        unit="ratio",
        scope=MetricScope.INFRASTRUCTURE,
        direction=MetricDirection.DESCRIPTIVE,
        source=MetricSource.INFRASTRUCTURE,
        version="1",
        value_kind=MetricValueKind.DISTRIBUTION,
    ),
    MetricDefinition(
        metric_id=RawMetricId.INFRASTRUCTURE_NETWORK_DISTANCE_P50_M,
        unit="m",
        scope=MetricScope.INFRASTRUCTURE,
        direction=MetricDirection.LOWER_IS_BETTER,
        source=MetricSource.INFRASTRUCTURE,
        version="1",
    ),
    MetricDefinition(
        metric_id=RawMetricId.INFRASTRUCTURE_NETWORK_DISTANCE_P90_M,
        unit="m",
        scope=MetricScope.INFRASTRUCTURE,
        direction=MetricDirection.LOWER_IS_BETTER,
        source=MetricSource.INFRASTRUCTURE,
        version="1",
    ),
    MetricDefinition(
        metric_id=RawMetricId.INFRASTRUCTURE_UNMET_DEMAND,
        unit="demand_units",
        scope=MetricScope.INFRASTRUCTURE,
        direction=MetricDirection.LOWER_IS_BETTER,
        source=MetricSource.INFRASTRUCTURE,
        version="1",
    ),
    MetricDefinition(
        metric_id=RawMetricId.INFRASTRUCTURE_CAPACITY_UTILIZATION,
        unit="ratio",
        scope=MetricScope.INFRASTRUCTURE,
        direction=MetricDirection.TARGET,
        source=MetricSource.INFRASTRUCTURE,
        version="1",
    ),
    MetricDefinition(
        metric_id=RawMetricId.CONSTRAINTS_HARD_VIOLATION_COUNT,
        unit="count",
        scope=MetricScope.CONSTRAINTS,
        direction=MetricDirection.LOWER_IS_BETTER,
        source=MetricSource.CONSTRAINTS,
        version="1",
    ),
    MetricDefinition(
        metric_id=RawMetricId.CONSTRAINTS_AFFECTED_AREA_M2,
        unit="m2",
        scope=MetricScope.CONSTRAINTS,
        direction=MetricDirection.LOWER_IS_BETTER,
        source=MetricSource.CONSTRAINTS,
        version="1",
    ),
    MetricDefinition(
        metric_id=RawMetricId.CONSTRAINTS_WEIGHTED_SOFT_PENALTY,
        unit="score",
        scope=MetricScope.CONSTRAINTS,
        direction=MetricDirection.LOWER_IS_BETTER,
        source=MetricSource.CONSTRAINTS,
        version="1",
    ),
)

CANONICAL_METRIC_REGISTRY = MetricRegistry(CANONICAL_RAW_METRIC_DEFINITIONS)
CANONICAL_RAW_METRIC_IDS = CANONICAL_METRIC_REGISTRY.metric_ids

if CANONICAL_RAW_METRIC_IDS != tuple(RawMetricId):
    raise BenchmarkContractError(
        "canonical metric registry must contain every RawMetricId exactly once "
        "in declaration order"
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
