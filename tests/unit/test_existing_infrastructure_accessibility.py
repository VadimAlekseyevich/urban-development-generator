from __future__ import annotations

import pytest

from core.urban_generator.demography import DemographicDemandCategory
from core.urban_generator.domain import (
    NetworkDistanceResult,
    NetworkGraphSnapshot,
    NetworkNodeRef,
    WorkingCRS,
)
from core.urban_generator.infrastructure import (
    ExistingInfrastructureFacilityRef,
    ExistingInfrastructureFacilitySnap,
    InfrastructureAccessibilityError,
    InfrastructureCandidatePolicy,
    InfrastructureCandidateSource,
    InfrastructureCategory,
    InfrastructureDemandModel,
    InfrastructureDemandRef,
    InfrastructureDemandSnap,
    InfrastructureNetworkSnapBatchResult,
    InfrastructureNetworkSnapDiagnostics,
    InfrastructureType,
    compute_existing_facility_accessibility,
)
from core.urban_generator.zoning import ZoneClass

WORKING_SRID = 3857
TYPE_CODE = "school.general"


class _RecordingNetworkBackend:
    def __init__(
        self,
        *,
        snapshot_id: str = "roads:v1",
        srid: int = WORKING_SRID,
        responses: tuple[NetworkDistanceResult, ...] = (),
    ) -> None:
        self._snapshot = NetworkGraphSnapshot(
            snapshot_id=snapshot_id,
            working_crs=WorkingCRS(srid=srid),
            node_count=20,
            edge_count=19,
            directed=False,
        )
        self.responses = responses
        self.calls: list[
            tuple[
                tuple[NetworkNodeRef, ...],
                tuple[NetworkNodeRef, ...],
                float | None,
            ]
        ] = []

    @property
    def snapshot(self) -> NetworkGraphSnapshot:
        return self._snapshot

    def snap(self, *args, **kwargs):
        raise AssertionError("existing accessibility must not call snap")

    def shortest_path(self, *args, **kwargs):
        raise AssertionError("existing accessibility must not call shortest_path")

    def multi_source_shortest_path(self, *args, **kwargs):
        raise AssertionError(
            "existing accessibility must not call multi_source_shortest_path"
        )

    def multi_source_distances(
        self,
        sources: tuple[NetworkNodeRef, ...],
        targets: tuple[NetworkNodeRef, ...],
        *,
        max_distance_m: float | None = None,
    ) -> tuple[NetworkDistanceResult, ...]:
        self.calls.append((sources, targets, max_distance_m))
        return self.responses


def _type(
    *,
    code: str = TYPE_CODE,
    max_network_distance_m: float = 1_500.0,
) -> InfrastructureType:
    return InfrastructureType(
        version="infrastructure-v1",
        code=code,
        category=InfrastructureCategory.EDUCATION,
        demand_model=InfrastructureDemandModel(
            signal=DemographicDemandCategory.AGE_GROUP,
            demographic_group="child",
            demand_rate=0.8,
        ),
        capacity=600.0,
        max_network_distance_m=max_network_distance_m,
        allowed_zones=(ZoneClass.PUBLIC,),
        minimum_site_area_m2=6_000.0,
        target_site_area_m2=12_000.0,
        candidate_policy=InfrastructureCandidatePolicy(
            sources=(InfrastructureCandidateSource.PARCEL,)
        ),
    )


def _demand(block_id: str, node_id: str, *, type_code: str = TYPE_CODE):
    return InfrastructureDemandSnap(
        ref=InfrastructureDemandRef(
            block_id=block_id,
            infrastructure_type_code=type_code,
        ),
        node=NetworkNodeRef(node_id=node_id),
        distance_m=1.0,
    )


def _facility(facility_id: str, node_id: str, *, type_code: str = TYPE_CODE):
    return ExistingInfrastructureFacilitySnap(
        ref=ExistingInfrastructureFacilityRef(
            facility_id=facility_id,
            infrastructure_type_code=type_code,
        ),
        node=NetworkNodeRef(node_id=node_id),
        distance_m=1.0,
    )


def _batch(
    *snapped: InfrastructureDemandSnap | ExistingInfrastructureFacilitySnap,
    snapshot_id: str = "roads:v1",
    srid: int = WORKING_SRID,
) -> InfrastructureNetworkSnapBatchResult:
    ordered = tuple(
        sorted(
            snapped,
            key=lambda item: (
                0 if isinstance(item, InfrastructureDemandSnap) else 1,
                item.ref.key,
            ),
        )
    )
    return InfrastructureNetworkSnapBatchResult(
        snapshot_id=snapshot_id,
        working_srid=srid,
        snapped=ordered,
        unsnapped=(),
        diagnostics=InfrastructureNetworkSnapDiagnostics(
            input_count=len(ordered),
            snapped_count=len(ordered),
            unsnapped_count=0,
            empty_network_count=0,
            no_node_within_max_distance_count=0,
        ),
    )


def test_existing_accessibility_uses_one_bounded_multi_source_call() -> None:
    backend = _RecordingNetworkBackend(
        responses=(
            NetworkDistanceResult(
                source=NetworkNodeRef(node_id="source-1"),
                target=NetworkNodeRef(node_id="target-1"),
                distance_m=100.0,
            ),
            NetworkDistanceResult(
                source=NetworkNodeRef(node_id="source-2"),
                target=NetworkNodeRef(node_id="target-2"),
                distance_m=250.0,
            ),
        )
    )
    snap_batch = _batch(
        _demand("block-a", "target-1"),
        _demand("block-b", "target-2"),
        _demand("block-c", "target-1"),
        _facility("facility-a", "source-1"),
        _facility("facility-b", "source-1"),
        _facility("facility-z", "source-2"),
    )

    result = compute_existing_facility_accessibility(
        backend,
        snap_batch,
        infrastructure_type=_type(),
    )

    assert backend.calls == [
        (
            (
                NetworkNodeRef(node_id="source-1"),
                NetworkNodeRef(node_id="source-2"),
            ),
            (
                NetworkNodeRef(node_id="target-1"),
                NetworkNodeRef(node_id="target-2"),
            ),
            1_500.0,
        )
    ]
    assert [item.key for item in result] == [
        (TYPE_CODE, "block-a", "existing_facility", "facility-a"),
        (TYPE_CODE, "block-b", "existing_facility", "facility-z"),
        (TYPE_CODE, "block-c", "existing_facility", "facility-a"),
    ]
    assert [item.distance_m for item in result] == [100.0, 250.0, 100.0]


def test_existing_accessibility_filters_to_requested_infrastructure_type() -> None:
    backend = _RecordingNetworkBackend(
        responses=(
            NetworkDistanceResult(
                source=NetworkNodeRef(node_id="school-source"),
                target=NetworkNodeRef(node_id="school-target"),
                distance_m=300.0,
            ),
        )
    )
    snap_batch = _batch(
        _demand("block-school", "school-target"),
        _demand("block-clinic", "clinic-target", type_code="clinic.primary"),
        _facility("school-1", "school-source"),
        _facility("clinic-1", "clinic-source", type_code="clinic.primary"),
    )

    result = compute_existing_facility_accessibility(
        backend,
        snap_batch,
        infrastructure_type=_type(),
    )

    assert backend.calls == [
        (
            (NetworkNodeRef(node_id="school-source"),),
            (NetworkNodeRef(node_id="school-target"),),
            1_500.0,
        )
    ]
    assert len(result) == 1
    assert result[0].demand_ref.block_id == "block-school"
    assert result[0].facility_site_ref == ExistingInfrastructureFacilityRef(
        facility_id="school-1",
        infrastructure_type_code=TYPE_CODE,
    )


def test_existing_accessibility_rejects_snapshot_mismatch_before_network_call() -> None:
    backend = _RecordingNetworkBackend(snapshot_id="roads:v2")

    with pytest.raises(
        InfrastructureAccessibilityError,
        match="snapshot_id must match",
    ):
        compute_existing_facility_accessibility(
            backend,
            _batch(
                _demand("block-a", "target-1"),
                _facility("facility-a", "source-1"),
            ),
            infrastructure_type=_type(),
        )

    assert backend.calls == []


def test_existing_accessibility_enforces_configured_subject_bound() -> None:
    backend = _RecordingNetworkBackend()

    with pytest.raises(
        InfrastructureAccessibilityError,
        match="subject limit exceeded",
    ):
        compute_existing_facility_accessibility(
            backend,
            _batch(
                _demand("block-a", "target-1"),
                _facility("facility-a", "source-1"),
            ),
            infrastructure_type=_type(),
            max_subjects=1,
        )

    assert backend.calls == []


def test_existing_accessibility_without_both_selected_sides_skips_network() -> None:
    backend = _RecordingNetworkBackend()

    result = compute_existing_facility_accessibility(
        backend,
        _batch(_demand("block-a", "target-1")),
        infrastructure_type=_type(),
    )

    assert result == ()
    assert backend.calls == []


def test_existing_accessibility_rejects_backend_result_beyond_type_cutoff() -> None:
    backend = _RecordingNetworkBackend(
        responses=(
            NetworkDistanceResult(
                source=NetworkNodeRef(node_id="source-1"),
                target=NetworkNodeRef(node_id="target-1"),
                distance_m=1_501.0,
            ),
        )
    )

    with pytest.raises(
        InfrastructureAccessibilityError,
        match="must not exceed max_network_distance_m",
    ):
        compute_existing_facility_accessibility(
            backend,
            _batch(
                _demand("block-a", "target-1"),
                _facility("facility-a", "source-1"),
            ),
            infrastructure_type=_type(max_network_distance_m=1_500.0),
        )
