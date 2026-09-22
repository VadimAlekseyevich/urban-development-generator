from __future__ import annotations

import math
from dataclasses import dataclass

from core.urban_generator.domain import (
    CANONICAL_METRIC_REGISTRY,
    MetricSource,
    RawMetricId,
)
from core.urban_generator.stages.roads import RoadStageOutput


class RoadMetricAdapterError(ValueError):
    """Raised when S11-T06 road metric artifacts violate the adapter contract."""


ROAD_RAW_METRIC_IDS = tuple(
    definition.metric_id
    for definition in CANONICAL_METRIC_REGISTRY.definitions_for(
        source=MetricSource.ROADS
    )
)


@dataclass(frozen=True, slots=True)
class RoadRawMetricValue:
    """One canonical road raw metric projected from S06 artifacts."""

    metric_id: RawMetricId
    scalar_value: float | None

    def __post_init__(self) -> None:
        if self.metric_id not in ROAD_RAW_METRIC_IDS:
            raise RoadMetricAdapterError(
                f"not a road RawMetricId: {self.metric_id!r}"
            )
        if self.scalar_value is None:
            if self.metric_id is not RawMetricId.ROADS_CIRCUITY:
                raise RoadMetricAdapterError(
                    "only roads.circuity may be missing"
                )
            return

        scalar = _require_non_negative_finite(
            "road scalar metric",
            self.scalar_value,
        )
        if (
            self.metric_id is RawMetricId.ROADS_DEAD_END_RATIO
            and scalar > 1.0
        ):
            raise RoadMetricAdapterError(
                "roads.dead_end_ratio must stay inside 0..1"
            )
        object.__setattr__(self, "scalar_value", scalar)


@dataclass(frozen=True, slots=True)
class RoadMetricAdapterDiagnostics:
    node_count: int
    edge_count: int
    component_count: int
    intersection_count: int
    circuity_edge_count: int
    dead_end_node_count: int

    def __post_init__(self) -> None:
        for field_name in (
            "node_count",
            "edge_count",
            "component_count",
            "intersection_count",
            "circuity_edge_count",
            "dead_end_node_count",
        ):
            value = getattr(self, field_name)
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 0
            ):
                raise RoadMetricAdapterError(
                    f"{field_name} must be a non-negative integer"
                )
        if self.intersection_count > self.node_count:
            raise RoadMetricAdapterError(
                "intersection_count cannot exceed node_count"
            )
        if self.dead_end_node_count > self.node_count:
            raise RoadMetricAdapterError(
                "dead_end_node_count cannot exceed node_count"
            )
        if self.circuity_edge_count > self.edge_count:
            raise RoadMetricAdapterError(
                "circuity_edge_count cannot exceed edge_count"
            )


@dataclass(frozen=True, slots=True)
class RoadRawMetricsResult:
    raw_metrics: tuple[RoadRawMetricValue, ...]
    diagnostics: RoadMetricAdapterDiagnostics

    def __post_init__(self) -> None:
        if not isinstance(self.raw_metrics, tuple):
            raise RoadMetricAdapterError(
                "raw_metrics must be an immutable tuple"
            )
        if any(
            not isinstance(item, RoadRawMetricValue)
            for item in self.raw_metrics
        ):
            raise RoadMetricAdapterError(
                "raw_metrics must contain RoadRawMetricValue values"
            )
        actual_ids = tuple(item.metric_id for item in self.raw_metrics)
        if actual_ids != ROAD_RAW_METRIC_IDS:
            raise RoadMetricAdapterError(
                "raw_metrics must contain every canonical road metric "
                "in registry order"
            )
        if not isinstance(
            self.diagnostics,
            RoadMetricAdapterDiagnostics,
        ):
            raise RoadMetricAdapterError(
                "diagnostics must be RoadMetricAdapterDiagnostics"
            )

    def require(self, metric_id: RawMetricId) -> RoadRawMetricValue:
        if metric_id not in ROAD_RAW_METRIC_IDS:
            raise RoadMetricAdapterError(
                f"not a road RawMetricId: {metric_id!r}"
            )
        return self.raw_metrics[ROAD_RAW_METRIC_IDS.index(metric_id)]


class RoadMetricAdapter:
    """Project authoritative S06 road metrics/validation without graph rebuild."""

    version = "1"

    def adapt(self, *, roads: RoadStageOutput) -> RoadRawMetricsResult:
        if not isinstance(roads, RoadStageOutput):
            raise RoadMetricAdapterError(
                "roads must be RoadStageOutput"
            )

        metrics = roads.metrics
        validation = roads.validation.diagnostics
        self._validate_artifact_alignment(roads=roads)

        values_by_id = {
            RawMetricId.ROADS_LENGTH_DENSITY_KM_PER_KM2: (
                RoadRawMetricValue(
                    metric_id=RawMetricId.ROADS_LENGTH_DENSITY_KM_PER_KM2,
                    scalar_value=metrics.length_density_km_per_km2,
                )
            ),
            RawMetricId.ROADS_CONNECTED_COMPONENTS: RoadRawMetricValue(
                metric_id=RawMetricId.ROADS_CONNECTED_COMPONENTS,
                scalar_value=float(metrics.component_count),
            ),
            RawMetricId.ROADS_AVERAGE_DEGREE: RoadRawMetricValue(
                metric_id=RawMetricId.ROADS_AVERAGE_DEGREE,
                scalar_value=metrics.mean_degree,
            ),
            RawMetricId.ROADS_INTERSECTION_DENSITY_PER_KM2: (
                RoadRawMetricValue(
                    metric_id=(
                        RawMetricId.ROADS_INTERSECTION_DENSITY_PER_KM2
                    ),
                    scalar_value=metrics.intersection_density_per_km2,
                )
            ),
            RawMetricId.ROADS_CIRCUITY: RoadRawMetricValue(
                metric_id=RawMetricId.ROADS_CIRCUITY,
                scalar_value=metrics.edge_weighted_circuity,
            ),
            RawMetricId.ROADS_DEAD_END_RATIO: RoadRawMetricValue(
                metric_id=RawMetricId.ROADS_DEAD_END_RATIO,
                scalar_value=validation.dead_end_ratio,
            ),
        }

        return RoadRawMetricsResult(
            raw_metrics=tuple(
                values_by_id[metric_id]
                for metric_id in ROAD_RAW_METRIC_IDS
            ),
            diagnostics=RoadMetricAdapterDiagnostics(
                node_count=metrics.node_count,
                edge_count=metrics.edge_count,
                component_count=metrics.component_count,
                intersection_count=metrics.intersection_count,
                circuity_edge_count=metrics.circuity_edge_count,
                dead_end_node_count=validation.dead_end_node_count,
            ),
        )

    @staticmethod
    def _validate_artifact_alignment(
        *,
        roads: RoadStageOutput,
    ) -> None:
        metrics = roads.metrics
        validation = roads.validation.diagnostics
        graph = roads.graph

        if metrics.node_count != validation.node_count:
            raise RoadMetricAdapterError(
                "road metrics and validation node_count must match"
            )
        if metrics.edge_count != validation.edge_count:
            raise RoadMetricAdapterError(
                "road metrics and validation edge_count must match"
            )
        if metrics.component_count != validation.component_count:
            raise RoadMetricAdapterError(
                "road metrics and validation component_count must match"
            )
        if metrics.node_count != len(graph.nodes):
            raise RoadMetricAdapterError(
                "road metrics node_count must match stage graph artifact"
            )
        if metrics.edge_count != len(graph.edges):
            raise RoadMetricAdapterError(
                "road metrics edge_count must match stage graph artifact"
            )
        if metrics.component_count != graph.diagnostics.component_count:
            raise RoadMetricAdapterError(
                "road metrics component_count must match stage graph artifact"
            )

        _require_non_negative_finite(
            "road analysis_area_m2",
            metrics.analysis_area_m2,
        )
        if metrics.analysis_area_m2 <= 0.0:
            raise RoadMetricAdapterError(
                "road analysis_area_m2 must be positive"
            )
        if (
            metrics.edge_weighted_circuity is None
            and metrics.circuity_edge_count != 0
        ):
            raise RoadMetricAdapterError(
                "missing road circuity requires zero circuity_edge_count"
            )
        if (
            metrics.edge_weighted_circuity is not None
            and metrics.circuity_edge_count <= 0
        ):
            raise RoadMetricAdapterError(
                "present road circuity requires positive circuity_edge_count"
            )
        if validation.dead_end_ratio < 0.0 or validation.dead_end_ratio > 1.0:
            raise RoadMetricAdapterError(
                "road validation dead_end_ratio must stay inside 0..1"
            )


def _require_non_negative_finite(
    field_name: str,
    value: float | int,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RoadMetricAdapterError(
            f"{field_name} must be a finite non-negative number"
        )
    numeric = float(value)
    if not math.isfinite(numeric) or numeric < 0.0:
        raise RoadMetricAdapterError(
            f"{field_name} must be a finite non-negative number"
        )
    return numeric
