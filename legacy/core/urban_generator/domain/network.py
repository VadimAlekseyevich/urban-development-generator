from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
from typing import Protocol, runtime_checkable

from core.urban_generator.domain.crs import WorkingCRS


class NetworkContractError(ValueError):
    """Raised when a network-domain contract is invalid."""


class NetworkRoutingAlgorithm(StrEnum):
    """Shortest-path algorithms supported by the backend-independent routing port."""

    DIJKSTRA = "dijkstra"
    ASTAR = "astar"


@dataclass(frozen=True, slots=True)
class NetworkNodeRef:
    """Backend-independent reference to a node inside a network snapshot."""

    node_id: str

    def __post_init__(self) -> None:
        _require_non_empty_string(self.node_id, "node_id")


@dataclass(frozen=True, slots=True)
class NetworkPoint:
    """Point expressed in the graph snapshot working CRS, in metre axis units."""

    x_m: float
    y_m: float

    def __post_init__(self) -> None:
        _require_finite_number(self.x_m, "x_m")
        _require_finite_number(self.y_m, "y_m")


@dataclass(frozen=True, slots=True)
class NetworkGraphSnapshot:
    """Immutable metadata identifying one routable graph snapshot."""

    snapshot_id: str
    working_crs: WorkingCRS
    node_count: int
    edge_count: int
    directed: bool

    def __post_init__(self) -> None:
        _require_non_empty_string(self.snapshot_id, "snapshot_id")
        if not isinstance(self.working_crs, WorkingCRS):
            raise NetworkContractError("working_crs must be a validated WorkingCRS")
        _require_non_negative_int(self.node_count, "node_count")
        _require_non_negative_int(self.edge_count, "edge_count")
        if not isinstance(self.directed, bool):
            raise NetworkContractError("directed must be a bool")


@dataclass(frozen=True, slots=True)
class NetworkSnapResult:
    """Nearest routable node for a point, with metric snap distance."""

    node: NetworkNodeRef
    distance_m: float

    def __post_init__(self) -> None:
        if not isinstance(self.node, NetworkNodeRef):
            raise NetworkContractError("node must be a NetworkNodeRef")
        _require_non_negative_finite_number(self.distance_m, "distance_m")


@dataclass(frozen=True, slots=True)
class NetworkPath:
    """Shortest-path result expressed only with domain references and metres."""

    nodes: tuple[NetworkNodeRef, ...]
    distance_m: float

    def __post_init__(self) -> None:
        if not self.nodes:
            raise NetworkContractError("path nodes must not be empty")
        if any(not isinstance(node, NetworkNodeRef) for node in self.nodes):
            raise NetworkContractError("path nodes must contain only NetworkNodeRef values")
        _require_non_negative_finite_number(self.distance_m, "distance_m")


@dataclass(frozen=True, slots=True)
class NetworkDistanceResult:
    """Nearest-source network distance for one target node."""

    source: NetworkNodeRef
    target: NetworkNodeRef
    distance_m: float

    def __post_init__(self) -> None:
        if not isinstance(self.source, NetworkNodeRef):
            raise NetworkContractError("source must be a NetworkNodeRef")
        if not isinstance(self.target, NetworkNodeRef):
            raise NetworkContractError("target must be a NetworkNodeRef")
        _require_non_negative_finite_number(self.distance_m, "distance_m")


@runtime_checkable
class NetworkBackend(Protocol):
    """Routing port implemented by infrastructure adapters such as NetworkX."""

    @property
    def snapshot(self) -> NetworkGraphSnapshot:
        """Return immutable metadata for the graph served by this backend."""

        ...

    def snap(
        self,
        point: NetworkPoint,
        *,
        max_distance_m: float,
    ) -> NetworkSnapResult | None:
        """Snap a working-CRS point to the graph within a bounded metric radius."""

        ...

    def shortest_path(
        self,
        source: NetworkNodeRef,
        target: NetworkNodeRef,
        *,
        algorithm: NetworkRoutingAlgorithm = NetworkRoutingAlgorithm.DIJKSTRA,
        max_distance_m: float | None = None,
    ) -> NetworkPath | None:
        """Return a shortest path with an explicit algorithm and optional distance bound."""

        ...

    def multi_source_shortest_path(
        self,
        sources: tuple[NetworkNodeRef, ...],
        target: NetworkNodeRef,
        *,
        algorithm: NetworkRoutingAlgorithm = NetworkRoutingAlgorithm.DIJKSTRA,
        max_distance_m: float | None = None,
    ) -> NetworkPath | None:
        """Return the shortest path from the nearest source to one target."""

        ...

    def multi_source_distances(
        self,
        sources: tuple[NetworkNodeRef, ...],
        targets: tuple[NetworkNodeRef, ...],
        *,
        max_distance_m: float | None = None,
    ) -> tuple[NetworkDistanceResult, ...]:
        """Return nearest-source distances for explicit target nodes."""

        ...


def require_max_distance_m(value: float | None) -> float | None:
    """Validate optional routing bounds shared by backend implementations."""

    if value is None:
        return None
    _require_non_negative_finite_number(value, "max_distance_m")
    return float(value)


def require_node_refs(
    values: tuple[NetworkNodeRef, ...],
    *,
    field_name: str,
    allow_empty: bool = False,
) -> tuple[NetworkNodeRef, ...]:
    """Validate immutable node-reference collections at the port boundary."""

    if not isinstance(values, tuple):
        raise NetworkContractError(f"{field_name} must be a tuple")
    if not allow_empty and not values:
        raise NetworkContractError(f"{field_name} must not be empty")
    if any(not isinstance(value, NetworkNodeRef) for value in values):
        raise NetworkContractError(f"{field_name} must contain only NetworkNodeRef values")
    return values


def require_routing_algorithm(value: NetworkRoutingAlgorithm) -> NetworkRoutingAlgorithm:
    """Validate the typed shortest-path algorithm selection at the port boundary."""

    if not isinstance(value, NetworkRoutingAlgorithm):
        raise NetworkContractError("algorithm must be a NetworkRoutingAlgorithm")
    return value


def _require_non_empty_string(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise NetworkContractError(f"{field_name} must be a non-empty string")
    if "\n" in value or "\r" in value:
        raise NetworkContractError(f"{field_name} must not contain line breaks")


def _require_non_negative_int(value: int, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise NetworkContractError(f"{field_name} must be a non-negative integer")


def _require_finite_number(value: float, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        raise NetworkContractError(f"{field_name} must be a finite number")


def _require_non_negative_finite_number(value: float, field_name: str) -> None:
    _require_finite_number(value, field_name)
    if value < 0:
        raise NetworkContractError(f"{field_name} must be non-negative")
