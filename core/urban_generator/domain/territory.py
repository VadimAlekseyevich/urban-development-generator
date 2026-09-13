import uuid
from dataclasses import dataclass
from enum import StrEnum

from core.urban_generator.domain.project import ProjectRef, ProjectSettings
from core.urban_generator.domain.semantics import WorldStateContract


class TerritorySnapshotError(ValueError):
    """Raised when a territory snapshot violates its immutable input contract."""


class SnapshotLayerKind(StrEnum):
    """Canonical input categories captured by a TerritorySnapshot."""

    BOUNDARY = "BOUNDARY"
    ROADS = "ROADS"
    BUILDINGS = "BUILDINGS"
    FACILITIES = "FACILITIES"
    LANDUSE = "LANDUSE"
    ZONES = "ZONES"
    WATER = "WATER"
    CONSTRAINTS = "CONSTRAINTS"
    DEM = "DEM"
    DEMOGRAPHY = "DEMOGRAPHY"


@dataclass(frozen=True, slots=True)
class SnapshotLayerRef:
    """Opaque reference to one fixed source layer used by a snapshot."""

    kind: SnapshotLayerKind
    source_ref: str

    def __post_init__(self) -> None:
        if not isinstance(self.kind, SnapshotLayerKind):
            raise TerritorySnapshotError("kind must be a SnapshotLayerKind value")
        if not isinstance(self.source_ref, str) or not self.source_ref.strip():
            raise TerritorySnapshotError("source_ref must be a non-empty opaque reference")

    @property
    def state_contract(self) -> WorldStateContract:
        return WorldStateContract.fixed_source()


@dataclass(frozen=True, slots=True)
class TerritorySnapshot:
    """Immutable, infrastructure-independent snapshot of fixed territory inputs."""

    snapshot_id: uuid.UUID
    project: ProjectRef
    settings: ProjectSettings
    boundary: SnapshotLayerRef
    roads: tuple[SnapshotLayerRef, ...] = ()
    buildings: tuple[SnapshotLayerRef, ...] = ()
    facilities: tuple[SnapshotLayerRef, ...] = ()
    landuse: tuple[SnapshotLayerRef, ...] = ()
    fixed_zones: tuple[SnapshotLayerRef, ...] = ()
    water: tuple[SnapshotLayerRef, ...] = ()
    constraints: tuple[SnapshotLayerRef, ...] = ()
    dem: tuple[SnapshotLayerRef, ...] = ()
    demography: tuple[SnapshotLayerRef, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.snapshot_id, uuid.UUID):
            raise TerritorySnapshotError("snapshot_id must be a UUID")
        if not isinstance(self.project, ProjectRef):
            raise TerritorySnapshotError("project must be a ProjectRef")
        if not isinstance(self.settings, ProjectSettings):
            raise TerritorySnapshotError("settings must be ProjectSettings")

        _require_layer("boundary", self.boundary, SnapshotLayerKind.BOUNDARY)
        _require_layers("roads", self.roads, SnapshotLayerKind.ROADS)
        _require_layers("buildings", self.buildings, SnapshotLayerKind.BUILDINGS)
        _require_layers("facilities", self.facilities, SnapshotLayerKind.FACILITIES)
        _require_layers("landuse", self.landuse, SnapshotLayerKind.LANDUSE)
        _require_layers("fixed_zones", self.fixed_zones, SnapshotLayerKind.ZONES)
        _require_layers("water", self.water, SnapshotLayerKind.WATER)
        _require_layers("constraints", self.constraints, SnapshotLayerKind.CONSTRAINTS)
        _require_layers("dem", self.dem, SnapshotLayerKind.DEM)
        _require_layers("demography", self.demography, SnapshotLayerKind.DEMOGRAPHY)

    @property
    def layer_refs(self) -> tuple[SnapshotLayerRef, ...]:
        return (
            self.boundary,
            *self.roads,
            *self.buildings,
            *self.facilities,
            *self.landuse,
            *self.fixed_zones,
            *self.water,
            *self.constraints,
            *self.dem,
            *self.demography,
        )

    @property
    def has_fixed_urban_state(self) -> bool:
        """Whether existing roads, buildings, facilities or zones are present."""

        return bool(self.roads or self.buildings or self.facilities or self.fixed_zones)


def _require_layer(
    field_name: str,
    layer: SnapshotLayerRef,
    expected_kind: SnapshotLayerKind,
) -> None:
    if not isinstance(layer, SnapshotLayerRef):
        raise TerritorySnapshotError(f"{field_name} must be a SnapshotLayerRef")
    if layer.kind is not expected_kind:
        raise TerritorySnapshotError(
            f"{field_name} must reference {expected_kind.value}, got {layer.kind.value}"
        )


def _require_layers(
    field_name: str,
    layers: tuple[SnapshotLayerRef, ...],
    expected_kind: SnapshotLayerKind,
) -> None:
    if not isinstance(layers, tuple):
        raise TerritorySnapshotError(f"{field_name} must be an immutable tuple of layer refs")
    for layer in layers:
        _require_layer(field_name, layer, expected_kind)
