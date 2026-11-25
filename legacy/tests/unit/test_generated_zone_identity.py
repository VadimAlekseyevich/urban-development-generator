import uuid

from shapely.geometry import box

from core.urban_generator.zoning import (
    GeneratedZoneRef,
    ZoneAssignment,
    ZoneAssignmentResult,
    ZoneClass,
    ZoneShareDiagnostic,
    ZoningPartitionCell,
    ZoningPartitionResult,
    ZoningSeed,
    build_generated_zone_refs,
    generated_zone_uuid,
)

WORKING_SRID = 32637
RUN_ID = uuid.UUID("00000000-0000-0000-0000-000000000777")


def _partition() -> ZoningPartitionResult:
    geometry = box(0.0, 0.0, 1.0, 1.0)
    seed = ZoningSeed(
        row=0,
        col=0,
        x_m=0.5,
        y_m=0.5,
        suitability_score=0.8,
    )
    return ZoningPartitionResult(
        working_srid=WORKING_SRID,
        developable_area=geometry,
        cells=(
            ZoningPartitionCell(
                seed_index=0,
                seed=seed,
                geometry=geometry,
                area_m2=1.0,
                validity_repaired=False,
            ),
        ),
        developable_area_m2=1.0,
        covered_area_m2=1.0,
        uncovered_area_m2=0.0,
        overlap_area_m2=0.0,
        developable_validity_repaired=False,
        repaired_cell_count=0,
    )


def _assignment() -> ZoneAssignmentResult:
    shares = tuple(
        ZoneShareDiagnostic(
            zone_class=zone_class,
            target_share=1.0 if zone_class is ZoneClass.RESIDENTIAL else 0.0,
            target_area_m2=1.0 if zone_class is ZoneClass.RESIDENTIAL else 0.0,
            assigned_area_m2=1.0 if zone_class is ZoneClass.RESIDENTIAL else 0.0,
            achieved_share=1.0 if zone_class is ZoneClass.RESIDENTIAL else 0.0,
            absolute_area_error_m2=0.0,
            cell_count=1 if zone_class is ZoneClass.RESIDENTIAL else 0,
        )
        for zone_class in ZoneClass
    )
    return ZoneAssignmentResult(
        assignments=(
            ZoneAssignment(
                cell_index=0,
                seed_index=0,
                zone_class=ZoneClass.RESIDENTIAL,
                area_m2=1.0,
                suitability_score=0.8,
            ),
        ),
        shares=shares,
        total_area_m2=1.0,
        zoning_config_version="1",
        zoning_config_fingerprint="a" * 64,
        strategy_version="1",
    )


def test_generated_zone_identity_is_stable_and_shared_by_ref_builder() -> None:
    expected = generated_zone_uuid(RUN_ID, cell_index=0, seed_index=0)

    refs = build_generated_zone_refs(
        run_id=RUN_ID,
        partition=_partition(),
        assignment=_assignment(),
    )

    assert refs == (
        GeneratedZoneRef(
            zone_id=str(expected),
            cell_index=0,
            seed_index=0,
            zone_class=ZoneClass.RESIDENTIAL,
            geometry=box(0.0, 0.0, 1.0, 1.0),
            area_m2=1.0,
            working_srid=WORKING_SRID,
        ),
    )
    assert generated_zone_uuid(RUN_ID, cell_index=0, seed_index=0) == expected
