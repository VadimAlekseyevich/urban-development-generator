import numpy as np
from shapely.geometry import LineString

from core.urban_generator.domain import NetworkPoint, WorldStateContract
from core.urban_generator.roads import (
    CandidateRoadAnchor,
    NodedRoad,
    RoadGraphBuilder,
    RoadGraphInput,
)
from core.urban_generator.roads.fixed_network_attachment import (
    FixedNetworkAttachmentConnector,
    FixedNetworkAttachmentPolicy,
    FixedNetworkAttachmentStatus,
)
from core.urban_generator.suitability import (
    HardExclusionMask,
    SuitabilityGridSpec,
    WeightedSuitabilityResult,
)
from core.urban_generator.zoning import ZoneClass

WORKING_SRID = 32637


def _surface() -> tuple[WeightedSuitabilityResult, HardExclusionMask]:
    grid = SuitabilityGridSpec(
        working_srid=WORKING_SRID,
        bounds=(0.0, 0.0, 4.0, 4.0),
        width=4,
        height=4,
    )
    hard = HardExclusionMask(
        grid=grid,
        excluded=np.zeros(grid.shape, dtype=np.bool_),
        source_codes=("boundary",),
    )
    suitability = WeightedSuitabilityResult(
        grid=grid,
        scores=np.ones(grid.shape, dtype=np.float64),
        valid_mask=np.ones(grid.shape, dtype=np.bool_),
        hard_excluded_mask=hard.excluded,
        config_version="1",
        config_fingerprint="a" * 64,
        factor_versions=(("constant", "1"),),
    )
    return suitability, hard


def _anchor(
    anchor_id: str,
    x: float,
    y: float,
    row: int,
    col: int,
) -> CandidateRoadAnchor:
    return CandidateRoadAnchor(
        anchor_id=anchor_id,
        point=NetworkPoint(x_m=x, y_m=y),
        zone_class=ZoneClass.RESIDENTIAL,
        zoning_cell_index=0,
        raster_row=row,
        raster_col=col,
        suitability_score=1.0,
    )


def _fixed_graph():
    return RoadGraphBuilder(working_srid=WORKING_SRID).build(
        (
            RoadGraphInput(
                road=NodedRoad(
                    road_id="fixed",
                    parts=(LineString(((0.0, 0.0), (4.0, 0.0))),),
                ),
                state=WorldStateContract.fixed_source(),
            ),
        )
    )


def test_generated_component_attaches_to_exact_fixed_graph_node() -> None:
    suitability, hard = _surface()
    anchor = _anchor("a", 0.5, 0.5, 3, 0)

    result = FixedNetworkAttachmentConnector(
        policy=FixedNetworkAttachmentPolicy(max_distance_m=2.0),
    ).attach(
        anchors=(anchor,),
        connected_pairs=(),
        fixed_graph=_fixed_graph(),
        suitability=suitability,
        hard_mask=hard,
    )

    assert result.complete is True
    attachment = result.attachments[0]
    assert attachment.status is FixedNetworkAttachmentStatus.CONNECTED
    assert attachment.fixed_node_id is not None
    assert attachment.geometry is not None
    assert tuple(attachment.geometry.coords)[-1] == (0.0, 0.0)
    assert result.diagnostics.attached_component_count == 1


def test_one_attachment_is_created_per_generated_connected_component() -> None:
    suitability, hard = _surface()
    anchors = (
        _anchor("a", 0.5, 0.5, 3, 0),
        _anchor("b", 1.5, 0.5, 3, 1),
        _anchor("c", 3.5, 0.5, 3, 3),
    )
    result = FixedNetworkAttachmentConnector(
        policy=FixedNetworkAttachmentPolicy(max_distance_m=5.0),
    ).attach(
        anchors=anchors,
        connected_pairs=(("a", "b"),),
        fixed_graph=_fixed_graph(),
        suitability=suitability,
        hard_mask=hard,
    )

    assert result.diagnostics.component_count == 2
    assert result.diagnostics.attached_component_count == 2
    assert result.complete is True


def test_component_without_fixed_node_in_range_is_explicitly_unattached() -> None:
    suitability, hard = _surface()
    anchor = _anchor("far", 3.5, 3.5, 0, 3)

    result = FixedNetworkAttachmentConnector(
        policy=FixedNetworkAttachmentPolicy(max_distance_m=1.0),
    ).attach(
        anchors=(anchor,),
        connected_pairs=(),
        fixed_graph=_fixed_graph(),
        suitability=suitability,
        hard_mask=hard,
    )

    assert result.complete is False
    assert (
        result.attachments[0].status
        is FixedNetworkAttachmentStatus.NO_FIXED_NODE_IN_RANGE
    )
