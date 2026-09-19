from __future__ import annotations

import networkx as nx
import pytest
from shapely.geometry import Point, box

from core.urban_generator.blocks import (
    BlockZoneAssociation,
    BlockZoneAssociationDiagnostics,
    BlockZoneAssociationResult,
    BlockZoneAssociationStatus,
    CleanedBlockCandidate,
    SplitBlockCandidate,
    ZoneAssociatedBlock,
)
from core.urban_generator.demography import DemographicDemandCategory
from core.urban_generator.domain import WorkingCRS
from core.urban_generator.infrastructure import (
    BlockInfrastructureDemand,
    ExistingInfrastructureDiagnostics,
    ExistingInfrastructureFacility,
    ExistingInfrastructureResult,
    InfrastructureCandidateGeometry,
    InfrastructureCandidateGeometryDiagnostics,
    InfrastructureCandidateGeometryKind,
    InfrastructureCandidateGeometryResult,
    InfrastructureCandidateSource,
    InfrastructureCategory,
    InfrastructureDemandSummary,
    InfrastructureNetworkSiteKind,
    InfrastructureNetworkSnapError,
    InfrastructureNetworkSnapper,
    UnmetDemandDiagnostics,
    UnmetDemandResult,
)
from core.urban_generator.roads.networkx_backend import NetworkXBackend
from core.urban_generator.zoning import ZoneClass

WORKING_SRID = 3857


def _backend() -> NetworkXBackend:
    graph = nx.Graph()
    graph.add_node("a", x_m=0.0, y_m=0.0)
    graph.add_node("b", x_m=100.0, y_m=0.0)
    graph.add_node("c", x_m=200.0, y_m=0.0)
    graph.add_edge("a", "b", length_m=100.0)
    graph.add_edge("b", "c", length_m=100.0)
    return NetworkXBackend(
        graph,
        snapshot_id="roads-snapshot-v1",
        working_crs=WorkingCRS(srid=WORKING_SRID),
    )


def _cleaned_block(block_id: str, *, center_x: float) -> CleanedBlockCandidate:
    geometry = box(center_x - 5.0, -5.0, center_x + 5.0, 5.0)
    member = SplitBlockCandidate(
        block_id="split:" + block_id,
        input_block_id="input:" + block_id,
        source_block_id="source:" + block_id,
        source_fragment_index=0,
        split_path=(),
        geometry=geometry,
    )
    return CleanedBlockCandidate(
        block_id=block_id,
        members=(member,),
        geometry=geometry,
    )


def _zoned_blocks(
    blocks: tuple[tuple[str, float], ...],
    *,
    working_srid: int = WORKING_SRID,
) -> BlockZoneAssociationResult:
    associated = tuple(
        ZoneAssociatedBlock(
            cleaned_block=_cleaned_block(block_id, center_x=center_x),
            association=BlockZoneAssociation(
                status=BlockZoneAssociationStatus.ASSOCIATED,
                zone_id="zone-1",
                zone_class=ZoneClass.RESIDENTIAL,
                positive_overlap_zone_ids=("zone-1",),
                maximum_overlap_ratio=1.0,
            ),
        )
        for block_id, center_x in blocks
    )
    return BlockZoneAssociationResult(
        working_crs=WorkingCRS(srid=working_srid),
        blocks=associated,
        diagnostics=BlockZoneAssociationDiagnostics(
            block_count=len(associated),
            zone_count=1,
            associated_block_count=len(associated),
            no_overlap_block_count=0,
            partial_overlap_block_count=0,
            ambiguous_block_count=0,
            spatial_candidate_pair_count=len(associated),
            positive_overlap_pair_count=len(associated),
        ),
    )


def _demand(
    block_ids: tuple[str, ...],
) -> UnmetDemandResult:
    types = (
        (
            "school.general",
            InfrastructureCategory.EDUCATION,
            DemographicDemandCategory.AGE_GROUP,
            "child",
        ),
        (
            "retail.local",
            InfrastructureCategory.RETAIL,
            DemographicDemandCategory.POPULATION,
            None,
        ),
    )
    demands = tuple(
        BlockInfrastructureDemand(
            block_id=block_id,
            zone_id="zone-1",
            zone_class=ZoneClass.RESIDENTIAL,
            infrastructure_type_code=type_code,
            infrastructure_category=category,
            demographic_signal=signal,
            demographic_group=group,
            source_signal_value=10.0,
            demand_rate=0.5,
            gross_demand=5.0,
            served_demand=0.0,
            unmet_demand=5.0,
        )
        for block_id in block_ids
        for type_code, category, signal, group in types
    )
    summaries = tuple(
        InfrastructureDemandSummary(
            infrastructure_type_code=type_code,
            infrastructure_category=category,
            demographic_signal=signal,
            demographic_group=group,
            gross_demand=5.0 * len(block_ids),
            served_demand=0.0,
            unmet_demand=5.0 * len(block_ids),
            existing_capacity=0.0,
        )
        for type_code, category, signal, group in types
    )
    return UnmetDemandResult(
        scenario_version="demography-v1",
        scenario_fingerprint="a" * 64,
        demands=tuple(sorted(demands, key=lambda item: item.key)),
        summaries=tuple(
            sorted(
                summaries,
                key=lambda item: item.infrastructure_type_code,
            )
        ),
        diagnostics=UnmetDemandDiagnostics(
            block_count=len(block_ids),
            infrastructure_type_count=2,
            demand_item_count=len(block_ids) * 2,
            served_assignment_count=0,
            existing_facility_count=0,
        ),
    )


def _candidate_geometry(
    *,
    candidate_id: str = "school.general:parcel:parcel-1",
    anchor_x: float = 95.0,
    working_srid: int = WORKING_SRID,
) -> InfrastructureCandidateGeometryResult:
    site = box(anchor_x - 2.0, -2.0, anchor_x + 2.0, 2.0)
    item = InfrastructureCandidateGeometry(
        candidate_id=candidate_id,
        infrastructure_type_code="school.general",
        source_kind=InfrastructureCandidateSource.PARCEL,
        source_id="parcel-1",
        zone_class=ZoneClass.PUBLIC,
        working_srid=working_srid,
        anchor=Point(anchor_x, 0.0),
        kind=InfrastructureCandidateGeometryKind.SITE,
        site_geometry=site,
        site_area_m2=float(site.area),
        zone_id="zone-public",
        block_id="block-site",
    )
    return InfrastructureCandidateGeometryResult(
        infrastructure_type_code="school.general",
        working_srid=working_srid,
        candidates=(item,),
        diagnostics=InfrastructureCandidateGeometryDiagnostics(
            candidate_count=1,
            polygon_site_count=1,
            host_building_count=0,
            clipped_site_count=1,
            full_source_site_count=0,
        ),
    )


def _existing(
    *,
    x: float = 205.0,
    working_srid: int = WORKING_SRID,
) -> ExistingInfrastructureResult:
    return ExistingInfrastructureResult(
        snapshot_id="territory-v1",
        facilities=(
            ExistingInfrastructureFacility(
                facility_id="existing:clinic-1",
                source_ref="dataset:facilities:v1",
                source_feature_id="clinic-1",
                infrastructure_type_code="clinic.primary",
                capacity=100.0,
                geometry=Point(x, 0.0),
                working_srid=working_srid,
            ),
        ),
        diagnostics=ExistingInfrastructureDiagnostics(
            input_count=1,
            mapped_count=1,
            skipped_unmapped_count=0,
            source_layer_count=1,
            infrastructure_type_count=1,
        ),
    )


def test_snapper_reuses_one_demand_snap_for_multiple_types() -> None:
    result = InfrastructureNetworkSnapper(
        _backend(),
        max_snap_distance_m=20.0,
    ).snap(
        _demand(("block-1",)),
        zoned_blocks=_zoned_blocks((("block-1", 10.0),)),
        candidate_geometries=(_candidate_geometry(),),
        existing=_existing(),
    )

    assert result.network_snapshot_id == "roads-snapshot-v1"
    assert result.working_srid == WORKING_SRID
    assert len(result.demand_snaps) == 1
    assert result.demand_snaps[0].block_id == "block-1"
    assert result.demand_snaps[0].node.node_id == "a"
    assert result.demand_snaps[0].snap_distance_m == pytest.approx(10.0)

    assert [(item.site_kind, item.node.node_id) for item in result.site_snaps] == [
        (InfrastructureNetworkSiteKind.CANDIDATE, "b"),
        (InfrastructureNetworkSiteKind.EXISTING, "c"),
    ]
    assert result.site_snaps[0].snap_distance_m == pytest.approx(5.0)
    assert result.site_snaps[1].snap_distance_m == pytest.approx(5.0)
    assert result.unsnapped_demand_block_ids == ()
    assert result.unsnapped_sites == ()
    assert result.diagnostics.demand_subject_count == 1
    assert result.diagnostics.candidate_site_count == 1
    assert result.diagnostics.existing_site_count == 1


def test_off_network_demand_and_sites_remain_explicitly_unsnapped() -> None:
    result = InfrastructureNetworkSnapper(
        _backend(),
        max_snap_distance_m=10.0,
    ).snap(
        _demand(("block-far",)),
        zoned_blocks=_zoned_blocks((("block-far", 500.0),)),
        candidate_geometries=(
            _candidate_geometry(anchor_x=400.0),
        ),
        existing=_existing(x=600.0),
    )

    assert result.demand_snaps == ()
    assert result.site_snaps == ()
    assert result.unsnapped_demand_block_ids == ("block-far",)
    assert [
        (item.site_kind, item.site_id)
        for item in result.unsnapped_sites
    ] == [
        (
            InfrastructureNetworkSiteKind.CANDIDATE,
            "school.general:parcel:parcel-1",
        ),
        (
            InfrastructureNetworkSiteKind.EXISTING,
            "existing:clinic-1",
        ),
    ]
    assert result.diagnostics.unsnapped_demand_count == 1
    assert result.diagnostics.unsnapped_site_count == 2


def test_demand_block_must_exist_in_zoned_block_snapshot() -> None:
    with pytest.raises(
        InfrastructureNetworkSnapError,
        match="missing from zoned blocks",
    ):
        InfrastructureNetworkSnapper(
            _backend(),
            max_snap_distance_m=20.0,
        ).snap(
            _demand(("block-missing",)),
            zoned_blocks=_zoned_blocks((("block-other", 0.0),)),
        )


def test_working_srid_must_match_network_snapshot() -> None:
    with pytest.raises(
        InfrastructureNetworkSnapError,
        match="block working SRID",
    ):
        InfrastructureNetworkSnapper(
            _backend(),
            max_snap_distance_m=20.0,
        ).snap(
            _demand(("block-1",)),
            zoned_blocks=_zoned_blocks(
                (("block-1", 0.0),),
                working_srid=32637,
            ),
        )

    with pytest.raises(
        InfrastructureNetworkSnapError,
        match="candidate working SRID",
    ):
        InfrastructureNetworkSnapper(
            _backend(),
            max_snap_distance_m=20.0,
        ).snap(
            _demand(("block-1",)),
            zoned_blocks=_zoned_blocks((("block-1", 0.0),)),
            candidate_geometries=(
                _candidate_geometry(working_srid=32637),
            ),
        )


def test_subject_limit_counts_unique_demand_blocks_and_all_sites() -> None:
    with pytest.raises(
        InfrastructureNetworkSnapError,
        match="subject limit exceeded",
    ):
        InfrastructureNetworkSnapper(
            _backend(),
            max_snap_distance_m=20.0,
            max_subjects=2,
        ).snap(
            _demand(("block-1",)),
            zoned_blocks=_zoned_blocks((("block-1", 0.0),)),
            candidate_geometries=(_candidate_geometry(),),
            existing=_existing(),
        )


def test_duplicate_candidate_site_id_is_rejected_across_results() -> None:
    candidate = _candidate_geometry()

    with pytest.raises(
        InfrastructureNetworkSnapError,
        match="duplicate candidate site id",
    ):
        InfrastructureNetworkSnapper(
            _backend(),
            max_snap_distance_m=20.0,
        ).snap(
            _demand(("block-1",)),
            zoned_blocks=_zoned_blocks((("block-1", 0.0),)),
            candidate_geometries=(candidate, candidate),
        )


def test_snap_radius_is_distinct_from_service_catchment_distance() -> None:
    result = InfrastructureNetworkSnapper(
        _backend(),
        max_snap_distance_m=4.0,
    ).snap(
        _demand(("block-1",)),
        zoned_blocks=_zoned_blocks((("block-1", 10.0),)),
    )

    assert result.max_snap_distance_m == pytest.approx(4.0)
    assert result.unsnapped_demand_block_ids == ("block-1",)
