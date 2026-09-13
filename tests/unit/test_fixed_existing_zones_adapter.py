import uuid

import pytest

from core.urban_generator.domain import (
    ProjectRef,
    ProjectSettings,
    SnapshotLayerKind,
    SnapshotLayerRef,
    TerritorySnapshot,
    TerritorySnapshotError,
)
from core.urban_generator.zoning import (
    FixedExistingZonesAdapter,
    FixedExistingZonesAdapterError,
)


def _layer(kind: SnapshotLayerKind, source_ref: str) -> SnapshotLayerRef:
    return SnapshotLayerRef(kind=kind, source_ref=source_ref)


def _snapshot(*, fixed_zones: tuple[SnapshotLayerRef, ...] = ()) -> TerritorySnapshot:
    return TerritorySnapshot(
        snapshot_id=uuid.uuid4(),
        project=ProjectRef(project_id=uuid.uuid4()),
        settings=ProjectSettings(working_srid=32637),
        boundary=_layer(SnapshotLayerKind.BOUNDARY, "dataset:boundary:v1"),
        fixed_zones=fixed_zones,
    )


def test_adapter_creates_fixed_zone_refs_without_mutating_sources() -> None:
    adapter = FixedExistingZonesAdapter()
    first = _layer(SnapshotLayerKind.LANDUSE, "dataset:zoning:b")
    second = _layer(SnapshotLayerKind.LANDUSE, "dataset:zoning:a")
    source_layers = (first, second)

    adapted = adapter.adapt(source_layers)

    assert source_layers == (first, second)
    assert adapted == (
        _layer(SnapshotLayerKind.ZONES, "dataset:zoning:a"),
        _layer(SnapshotLayerKind.ZONES, "dataset:zoning:b"),
    )
    assert all(layer.state_contract.is_fixed_source for layer in adapted)


def test_adapter_is_deterministic_for_source_order() -> None:
    adapter = FixedExistingZonesAdapter()
    first = _layer(SnapshotLayerKind.LANDUSE, "dataset:zoning:a")
    second = _layer(SnapshotLayerKind.LANDUSE, "dataset:zoning:b")

    assert adapter.adapt((first, second)) == adapter.adapt((second, first))


def test_adapter_allows_no_existing_zones() -> None:
    assert FixedExistingZonesAdapter().adapt(()) == ()


def test_adapter_requires_immutable_tuple_input() -> None:
    layer = _layer(SnapshotLayerKind.LANDUSE, "dataset:zoning:a")

    with pytest.raises(FixedExistingZonesAdapterError, match="immutable tuple"):
        FixedExistingZonesAdapter().adapt([layer])  # type: ignore[arg-type]


def test_adapter_rejects_non_landuse_source_layers() -> None:
    layer = _layer(SnapshotLayerKind.ROADS, "dataset:roads:v1")

    with pytest.raises(FixedExistingZonesAdapterError, match="LANDUSE"):
        FixedExistingZonesAdapter().adapt((layer,))


def test_adapter_rejects_duplicate_source_refs() -> None:
    first = _layer(SnapshotLayerKind.LANDUSE, "dataset:zoning:v1")
    second = _layer(SnapshotLayerKind.LANDUSE, "dataset:zoning:v1")

    with pytest.raises(FixedExistingZonesAdapterError, match="unique"):
        FixedExistingZonesAdapter().adapt((first, second))


def test_fixed_zones_are_part_of_snapshot_fixed_state() -> None:
    fixed_zone = _layer(SnapshotLayerKind.ZONES, "dataset:zoning:v1")
    snapshot = _snapshot(fixed_zones=(fixed_zone,))

    assert snapshot.fixed_zones == (fixed_zone,)
    assert fixed_zone in snapshot.layer_refs
    assert snapshot.has_fixed_urban_state is True
    assert snapshot.fixed_zones[0].state_contract.is_fixed_source is True


def test_snapshot_rejects_non_zone_ref_in_fixed_zone_slot() -> None:
    wrong = _layer(SnapshotLayerKind.LANDUSE, "dataset:zoning:v1")

    with pytest.raises(TerritorySnapshotError, match="fixed_zones must reference ZONES"):
        _snapshot(fixed_zones=(wrong,))
