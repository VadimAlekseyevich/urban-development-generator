import uuid
from dataclasses import FrozenInstanceError

import pytest

from core.urban_generator.domain import (
    ProjectRef,
    ProjectSettings,
    SnapshotLayerKind,
    SnapshotLayerRef,
    TerritorySnapshot,
    TerritorySnapshotError,
)


def layer(kind: SnapshotLayerKind, name: str) -> SnapshotLayerRef:
    return SnapshotLayerRef(kind=kind, source_ref=f"synthetic:{name}:v1")


def test_synthetic_snapshot_can_be_created_without_database() -> None:
    snapshot = TerritorySnapshot(
        snapshot_id=uuid.uuid4(),
        project=ProjectRef(project_id=uuid.uuid4()),
        settings=ProjectSettings(working_srid=32637),
        boundary=layer(SnapshotLayerKind.BOUNDARY, "boundary"),
        roads=(layer(SnapshotLayerKind.ROADS, "roads"),),
        buildings=(layer(SnapshotLayerKind.BUILDINGS, "buildings"),),
        facilities=(layer(SnapshotLayerKind.FACILITIES, "facilities"),),
        landuse=(layer(SnapshotLayerKind.LANDUSE, "landuse"),),
        water=(layer(SnapshotLayerKind.WATER, "water"),),
        constraints=(layer(SnapshotLayerKind.CONSTRAINTS, "constraints"),),
        dem=(layer(SnapshotLayerKind.DEM, "dem"),),
        demography=(layer(SnapshotLayerKind.DEMOGRAPHY, "demography"),),
    )

    assert len(snapshot.layer_refs) == 9
    assert snapshot.has_fixed_urban_state is True
    assert snapshot.settings.working_srid == 32637
    assert all(ref.state_contract.is_fixed_source for ref in snapshot.layer_refs)


def test_snapshot_is_immutable() -> None:
    snapshot = TerritorySnapshot(
        snapshot_id=uuid.uuid4(),
        project=ProjectRef(project_id=uuid.uuid4()),
        settings=ProjectSettings(working_srid=32637),
        boundary=layer(SnapshotLayerKind.BOUNDARY, "boundary"),
    )

    with pytest.raises(FrozenInstanceError):
        setattr(snapshot, "snapshot_id", uuid.uuid4())

    assert snapshot.roads == ()


def test_snapshot_allows_empty_fixed_urban_state() -> None:
    snapshot = TerritorySnapshot(
        snapshot_id=uuid.uuid4(),
        project=ProjectRef(project_id=uuid.uuid4()),
        settings=ProjectSettings(working_srid=32637),
        boundary=layer(SnapshotLayerKind.BOUNDARY, "boundary"),
        water=(layer(SnapshotLayerKind.WATER, "water"),),
        dem=(layer(SnapshotLayerKind.DEM, "dem"),),
    )

    assert snapshot.has_fixed_urban_state is False


def test_snapshot_rejects_layer_in_wrong_slot() -> None:
    with pytest.raises(TerritorySnapshotError, match="boundary must reference BOUNDARY"):
        TerritorySnapshot(
            snapshot_id=uuid.uuid4(),
            project=ProjectRef(project_id=uuid.uuid4()),
            settings=ProjectSettings(working_srid=32637),
            boundary=layer(SnapshotLayerKind.ROADS, "not-boundary"),
        )


def test_layer_reference_requires_non_empty_opaque_reference() -> None:
    with pytest.raises(TerritorySnapshotError, match="non-empty"):
        SnapshotLayerRef(kind=SnapshotLayerKind.ROADS, source_ref="   ")
