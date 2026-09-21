from __future__ import annotations

import argparse
import hashlib
import json
import math
from dataclasses import asdict, dataclass
from time import perf_counter_ns

import networkx as nx

from core.urban_generator.demography import DemographicDemandCategory
from core.urban_generator.domain import (
    NetworkDistanceResult,
    NetworkNodeRef,
    WorkingCRS,
)
from core.urban_generator.infrastructure import (
    BlockInfrastructureDemand,
    InfrastructureCandidateAccessibilityPolicy,
    InfrastructureCandidatePolicy,
    InfrastructureCandidateRef,
    InfrastructureCandidateSnap,
    InfrastructureCandidateSource,
    InfrastructureCategory,
    InfrastructureDemandModel,
    InfrastructureDemandRef,
    InfrastructureDemandSnap,
    InfrastructureGreedyPlacementPolicy,
    InfrastructureGreedyPlacementState,
    InfrastructureNetworkSnapBatchResult,
    InfrastructureNetworkSnapDiagnostics,
    InfrastructurePlacementSelectionStatus,
    InfrastructureType,
    apply_infrastructure_greedy_selection,
    calculate_infrastructure_candidate_benefits,
    compute_candidate_site_accessibility_batch,
    initialize_infrastructure_greedy_placement_state,
    select_infrastructure_greedy_candidate,
)
from core.urban_generator.roads import NetworkXBackend
from core.urban_generator.zoning import ZoneClass

REFERENCE_FIXTURE_NAME = "infrastructure-candidate-demand-v1"
WORKING_SRID = 3857
SNAPSHOT_ID = "roads:infrastructure-reference-v1"
TYPE_CODE = "school.reference"


@dataclass(frozen=True, slots=True)
class InfrastructureBenchmarkConfig:
    """Bounded T07/T08 workload for routing/cache regression evidence."""

    demand_count: int = 64
    candidate_count: int = 16
    demand_batch_size: int = 16
    capacity: float = 4.0
    max_facilities: int = 8
    spacing_m: float = 10.0
    working_srid: int = WORKING_SRID

    def __post_init__(self) -> None:
        for name, value in (
            ("demand_count", self.demand_count),
            ("candidate_count", self.candidate_count),
            ("demand_batch_size", self.demand_batch_size),
            ("max_facilities", self.max_facilities),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        for name, value in (
            ("capacity", self.capacity),
            ("spacing_m", self.spacing_m),
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{name} must be a number")
            if not math.isfinite(float(value)) or float(value) <= 0.0:
                raise ValueError(f"{name} must be positive and finite")
        if self.demand_batch_size > self.demand_count:
            raise ValueError("demand_batch_size must not exceed demand_count")
        if self.max_facilities > self.candidate_count:
            raise ValueError("max_facilities must not exceed candidate_count")
        if self.max_facilities * self.capacity > self.demand_count:
            raise ValueError(
                "fixture must retain positive demand through every greedy iteration"
            )
        _ = WorkingCRS(self.working_srid)


@dataclass(frozen=True, slots=True)
class InfrastructureBenchmarkResult:
    fixture_name: str
    working_srid: int
    demand_count: int
    candidate_count: int
    subject_count: int
    reachable_count: int
    demand_batch_size: int
    expected_routing_call_count: int
    routing_call_count: int
    routing_call_count_after_greedy: int
    greedy_iteration_count: int
    accepted_facility_count: int
    remaining_demand: float
    accessibility_ms: float
    greedy_ms: float
    deterministic_digest: str


class _CountingNetworkXBackend(NetworkXBackend):
    def __init__(self, graph: nx.Graph, *, working_srid: int) -> None:
        self.routing_call_count = 0
        super().__init__(
            graph,
            snapshot_id=SNAPSHOT_ID,
            working_crs=WorkingCRS(srid=working_srid),
        )

    def multi_source_distances(
        self,
        sources: tuple[NetworkNodeRef, ...],
        targets: tuple[NetworkNodeRef, ...],
        *,
        max_distance_m: float | None = None,
    ) -> tuple[NetworkDistanceResult, ...]:
        self.routing_call_count += 1
        return super().multi_source_distances(
            sources,
            targets,
            max_distance_m=max_distance_m,
        )


def _elapsed_ms(start_ns: int, end_ns: int) -> float:
    return (end_ns - start_ns) / 1_000_000.0


def _type(config: InfrastructureBenchmarkConfig) -> InfrastructureType:
    max_distance_m = (
        config.demand_count + config.candidate_count
    ) * config.spacing_m
    return InfrastructureType(
        version="infrastructure-reference-v1",
        code=TYPE_CODE,
        category=InfrastructureCategory.EDUCATION,
        demand_model=InfrastructureDemandModel(
            signal=DemographicDemandCategory.AGE_GROUP,
            demographic_group="child",
            demand_rate=1.0,
        ),
        capacity=config.capacity,
        max_network_distance_m=max_distance_m,
        allowed_zones=(ZoneClass.PUBLIC,),
        minimum_site_area_m2=100.0,
        target_site_area_m2=200.0,
        candidate_policy=InfrastructureCandidatePolicy(
            sources=(InfrastructureCandidateSource.PARCEL,),
        ),
    )


def _graph(config: InfrastructureBenchmarkConfig) -> nx.Graph:
    graph = nx.Graph()
    total_nodes = config.demand_count + config.candidate_count
    for index in range(total_nodes):
        graph.add_node(
            f"node:{index:04d}",
            x_m=float(index) * config.spacing_m,
            y_m=0.0,
        )
    for index in range(total_nodes - 1):
        graph.add_edge(
            f"node:{index:04d}",
            f"node:{index + 1:04d}",
            length_m=config.spacing_m,
        )
    return graph


def _demands(
    config: InfrastructureBenchmarkConfig,
) -> tuple[BlockInfrastructureDemand, ...]:
    return tuple(
        BlockInfrastructureDemand(
            block_id=f"block:{index:04d}",
            zone_id="zone:reference",
            zone_class=ZoneClass.PUBLIC,
            infrastructure_type_code=TYPE_CODE,
            infrastructure_category=InfrastructureCategory.EDUCATION,
            demographic_signal=DemographicDemandCategory.AGE_GROUP,
            demographic_group="child",
            source_signal_value=1.0,
            demand_rate=1.0,
            gross_demand=1.0,
            served_demand=0.0,
            unmet_demand=1.0,
        )
        for index in range(config.demand_count)
    )


def _snap_batch(
    config: InfrastructureBenchmarkConfig,
) -> InfrastructureNetworkSnapBatchResult:
    demand_snaps = tuple(
        InfrastructureDemandSnap(
            ref=InfrastructureDemandRef(
                block_id=f"block:{index:04d}",
                infrastructure_type_code=TYPE_CODE,
            ),
            node=NetworkNodeRef(node_id=f"node:{index:04d}"),
            distance_m=0.0,
        )
        for index in range(config.demand_count)
    )
    candidate_snaps = tuple(
        InfrastructureCandidateSnap(
            ref=InfrastructureCandidateRef(
                candidate_id=f"candidate:{index:04d}",
                infrastructure_type_code=TYPE_CODE,
            ),
            node=NetworkNodeRef(
                node_id=f"node:{config.demand_count + index:04d}"
            ),
            distance_m=0.0,
        )
        for index in range(config.candidate_count)
    )
    snapped = (*demand_snaps, *candidate_snaps)
    return InfrastructureNetworkSnapBatchResult(
        snapshot_id=SNAPSHOT_ID,
        working_srid=config.working_srid,
        snapped=snapped,
        unsnapped=(),
        diagnostics=InfrastructureNetworkSnapDiagnostics(
            input_count=len(snapped),
            snapped_count=len(snapped),
            unsnapped_count=0,
            empty_network_count=0,
            no_node_within_max_distance_count=0,
        ),
    )


def _candidate_refs(
    config: InfrastructureBenchmarkConfig,
) -> tuple[InfrastructureCandidateRef, ...]:
    return tuple(
        InfrastructureCandidateRef(
            candidate_id=f"candidate:{index:04d}",
            infrastructure_type_code=TYPE_CODE,
        )
        for index in range(config.candidate_count)
    )


def run_reference_infrastructure_benchmark(
    config: InfrastructureBenchmarkConfig = InfrastructureBenchmarkConfig(),
) -> InfrastructureBenchmarkResult:
    infrastructure_type = _type(config)
    demands = _demands(config)
    snap_batch = _snap_batch(config)
    backend = _CountingNetworkXBackend(
        _graph(config),
        working_srid=config.working_srid,
    )

    subject_count = config.demand_count * config.candidate_count
    expected_routing_calls = config.candidate_count * math.ceil(
        config.demand_count / config.demand_batch_size
    )
    accessibility_policy = InfrastructureCandidateAccessibilityPolicy(
        max_candidates=config.candidate_count,
        max_demands=config.demand_count,
        demand_batch_size=config.demand_batch_size,
        max_routing_calls=expected_routing_calls,
        max_results=subject_count,
    )

    accessibility_started = perf_counter_ns()
    accessibility = compute_candidate_site_accessibility_batch(
        backend,
        snap_batch,
        infrastructure_type=infrastructure_type,
        policy=accessibility_policy,
    )
    accessibility_finished = perf_counter_ns()

    if accessibility.diagnostics.subject_count != subject_count:
        raise RuntimeError(
            "reference candidate×demand subject count changed: "
            f"{accessibility.diagnostics.subject_count} != {subject_count}"
        )
    if accessibility.diagnostics.reachable_count != subject_count:
        raise RuntimeError(
            "reference accessibility must keep the full matrix reachable"
        )
    if backend.routing_call_count != expected_routing_calls:
        raise RuntimeError(
            "reference routing-call count changed: "
            f"{backend.routing_call_count} != {expected_routing_calls}"
        )

    state = initialize_infrastructure_greedy_placement_state(
        demands,
        _candidate_refs(config),
        candidate_accessibility=accessibility,
    )
    placement_policy = InfrastructureGreedyPlacementPolicy(
        max_facilities=config.max_facilities,
        max_iterations=config.max_facilities,
    )

    greedy_started = perf_counter_ns()
    for iteration_index in range(config.max_facilities):
        benefits = calculate_infrastructure_candidate_benefits(
            state,
            infrastructure_type=infrastructure_type,
        )
        selection = select_infrastructure_greedy_candidate(
            state,
            benefits,
            iteration_index=iteration_index,
            policy=placement_policy,
        )
        if selection.status is not InfrastructurePlacementSelectionStatus.SELECTED:
            raise RuntimeError(
                "reference greedy fixture stopped before configured iterations"
            )
        state = apply_infrastructure_greedy_selection(
            state,
            selection,
            infrastructure_type=infrastructure_type,
        )
    greedy_finished = perf_counter_ns()

    routing_after_greedy = backend.routing_call_count
    if routing_after_greedy != expected_routing_calls:
        raise RuntimeError(
            "greedy placement repeated candidate×demand routing instead of "
            "using the T07 coverage cache"
        )

    remaining_demand = math.fsum(
        item.remaining_demand for item in state.remaining_demand
    )
    expected_remaining = (
        config.demand_count - config.max_facilities * config.capacity
    )
    if not math.isclose(
        remaining_demand,
        expected_remaining,
        rel_tol=1e-12,
        abs_tol=1e-9,
    ):
        raise RuntimeError(
            "reference greedy remaining demand changed: "
            f"{remaining_demand} != {expected_remaining}"
        )

    digest = hashlib.sha256()
    for accepted in state.accepted_facilities:
        digest.update(accepted.candidate_ref.candidate_id.encode("utf-8"))
        digest.update(str(accepted.acceptance_index).encode("ascii"))
    for item in state.remaining_demand:
        digest.update(item.demand_ref.block_id.encode("utf-8"))
        digest.update(repr(item.remaining_demand).encode("ascii"))

    return InfrastructureBenchmarkResult(
        fixture_name=REFERENCE_FIXTURE_NAME,
        working_srid=config.working_srid,
        demand_count=config.demand_count,
        candidate_count=config.candidate_count,
        subject_count=subject_count,
        reachable_count=accessibility.diagnostics.reachable_count,
        demand_batch_size=config.demand_batch_size,
        expected_routing_call_count=expected_routing_calls,
        routing_call_count=backend.routing_call_count,
        routing_call_count_after_greedy=routing_after_greedy,
        greedy_iteration_count=config.max_facilities,
        accepted_facility_count=len(state.accepted_facilities),
        remaining_demand=remaining_demand,
        accessibility_ms=_elapsed_ms(
            accessibility_started,
            accessibility_finished,
        ),
        greedy_ms=_elapsed_ms(greedy_started, greedy_finished),
        deterministic_digest=digest.hexdigest(),
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run deterministic S10 infrastructure reference workload."
    )
    parser.add_argument("--demand-count", type=int, default=64)
    parser.add_argument("--candidate-count", type=int, default=16)
    parser.add_argument("--demand-batch-size", type=int, default=16)
    parser.add_argument("--max-facilities", type=int, default=8)
    return parser


def main() -> None:
    args = _parser().parse_args()
    result = run_reference_infrastructure_benchmark(
        InfrastructureBenchmarkConfig(
            demand_count=args.demand_count,
            candidate_count=args.candidate_count,
            demand_batch_size=args.demand_batch_size,
            max_facilities=args.max_facilities,
        )
    )
    print(json.dumps(asdict(result), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
