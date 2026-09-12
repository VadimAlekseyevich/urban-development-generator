from dataclasses import dataclass
from math import hypot

import pytest

from core.urban_generator.domain import (
    NetworkBackend,
    NetworkContractError,
    NetworkDistanceResult,
    NetworkGraphSnapshot,
    NetworkNodeRef,
    NetworkPath,
    NetworkPoint,
    NetworkSnapResult,
    WorkingCRS,
    require_max_distance_m,
    require_node_refs,
)


@dataclass
class FakeNetworkBackend:
    snapshot: NetworkGraphSnapshot
    positions: dict[str, NetworkPoint]

    def snap(
        self,
        point: NetworkPoint,
        *,
        max_distance_m: float,
    ) -> NetworkSnapResult | None:
        limit = require_max_distance_m(max_distance_m)
        assert limit is not None
        candidates = [
            (
                hypot(position.x_m - point.x_m, position.y_m - point.y_m),
                NetworkNodeRef(node_id=node_id),
            )
            for node_id, position in self.positions.items()
        ]
        distance_m, node = min(candidates, key=lambda item: item[0])
        if distance_m > limit:
            return None
        return NetworkSnapResult(node=node, distance_m=distance_m)

    def shortest_path(
        self,
        source: NetworkNodeRef,
        target: NetworkNodeRef,
        *,
        max_distance_m: float | None = None,
    ) -> NetworkPath | None:
        limit = require_max_distance_m(max_distance_m)
        order = ("a", "b", "c")
        source_index = order.index(source.node_id)
        target_index = order.index(target.node_id)
        step = 1 if target_index >= source_index else -1
        path_ids = order[source_index : target_index + step : step]
        distance_m = abs(target_index - source_index) * 10.0
        if limit is not None and distance_m > limit:
            return None
        return NetworkPath(
            nodes=tuple(NetworkNodeRef(node_id=node_id) for node_id in path_ids),
            distance_m=distance_m,
        )

    def multi_source_distances(
        self,
        sources: tuple[NetworkNodeRef, ...],
        targets: tuple[NetworkNodeRef, ...],
        *,
        max_distance_m: float | None = None,
    ) -> tuple[NetworkDistanceResult, ...]:
        sources = require_node_refs(sources, field_name="sources")
        targets = require_node_refs(targets, field_name="targets", allow_empty=True)
        limit = require_max_distance_m(max_distance_m)
        order = {"a": 0, "b": 1, "c": 2}
        results: list[NetworkDistanceResult] = []
        for target in targets:
            source = min(
                sources,
                key=lambda candidate: abs(order[candidate.node_id] - order[target.node_id]),
            )
            distance_m = abs(order[source.node_id] - order[target.node_id]) * 10.0
            if limit is None or distance_m <= limit:
                results.append(
                    NetworkDistanceResult(
                        source=source,
                        target=target,
                        distance_m=distance_m,
                    )
                )
        return tuple(results)


def _backend() -> FakeNetworkBackend:
    return FakeNetworkBackend(
        snapshot=NetworkGraphSnapshot(
            snapshot_id="roads-v1",
            working_crs=WorkingCRS(srid=3857),
            node_count=3,
            edge_count=2,
            directed=False,
        ),
        positions={
            "a": NetworkPoint(x_m=0.0, y_m=0.0),
            "b": NetworkPoint(x_m=10.0, y_m=0.0),
            "c": NetworkPoint(x_m=20.0, y_m=0.0),
        },
    )


def test_fake_backend_satisfies_network_protocol_without_networkx() -> None:
    backend = _backend()

    assert isinstance(backend, NetworkBackend)
    assert backend.snapshot.snapshot_id == "roads-v1"
    assert backend.snapshot.working_crs.srid == 3857


def test_snap_is_bounded_and_reports_metric_distance() -> None:
    backend = _backend()

    result = backend.snap(NetworkPoint(x_m=9.0, y_m=0.0), max_distance_m=2.0)
    missed = backend.snap(NetworkPoint(x_m=9.0, y_m=0.0), max_distance_m=0.5)

    assert result == NetworkSnapResult(node=NetworkNodeRef("b"), distance_m=1.0)
    assert missed is None


def test_shortest_path_can_be_bounded_by_network_distance() -> None:
    backend = _backend()

    path = backend.shortest_path(NetworkNodeRef("a"), NetworkNodeRef("c"))
    bounded_out = backend.shortest_path(
        NetworkNodeRef("a"),
        NetworkNodeRef("c"),
        max_distance_m=15.0,
    )

    assert path == NetworkPath(
        nodes=(NetworkNodeRef("a"), NetworkNodeRef("b"), NetworkNodeRef("c")),
        distance_m=20.0,
    )
    assert bounded_out is None


def test_multi_source_distances_returns_nearest_source_per_target() -> None:
    backend = _backend()

    results = backend.multi_source_distances(
        (NetworkNodeRef("a"), NetworkNodeRef("c")),
        (NetworkNodeRef("b"), NetworkNodeRef("c")),
    )

    assert results == (
        NetworkDistanceResult(
            source=NetworkNodeRef("a"),
            target=NetworkNodeRef("b"),
            distance_m=10.0,
        ),
        NetworkDistanceResult(
            source=NetworkNodeRef("c"),
            target=NetworkNodeRef("c"),
            distance_m=0.0,
        ),
    )


def test_network_contract_rejects_invalid_bounds_and_references() -> None:
    with pytest.raises(NetworkContractError, match="max_distance_m must be non-negative"):
        require_max_distance_m(-1.0)

    with pytest.raises(NetworkContractError, match="sources must not be empty"):
        require_node_refs((), field_name="sources")

    with pytest.raises(NetworkContractError, match="node_id must be a non-empty string"):
        NetworkNodeRef("")

    with pytest.raises(NetworkContractError, match="distance_m must be non-negative"):
        NetworkPath(nodes=(NetworkNodeRef("a"),), distance_m=-1.0)
