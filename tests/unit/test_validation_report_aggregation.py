import json

import pytest
from shapely import to_wkb
from shapely.geometry import Polygon, box

from core.urban_generator.domain import (
    ConstraintContractError,
    ConstraintEntityRef,
    ConstraintProblemGeometry,
    ConstraintResult,
    ConstraintScope,
    ConstraintSeverity,
    VALIDATION_REPORT_SCHEMA_VERSION,
    ValidationReport,
    ValidationReportCodecError,
    aggregate_validation_reports,
    deserialize_validation_report,
    serialize_validation_report,
)


def _result(
    *,
    code: str,
    severity: ConstraintSeverity,
    scope: ConstraintScope,
    entity_id: str,
    geometry,
) -> ConstraintResult:
    return ConstraintResult(
        code=code,
        severity=severity,
        scope=scope,
        passed=False,
        message=f"{code} failed",
        entity_ref=ConstraintEntityRef(entity_id=entity_id),
        problem_geometry=ConstraintProblemGeometry(
            geometry=geometry,
            working_srid=32637,
        ),
    )


def test_cross_stage_aggregation_preserves_order_detail_and_semantics() -> None:
    road_result = _result(
        code="roads.connectivity",
        severity=ConstraintSeverity.HARD,
        scope=ConstraintScope.ROAD,
        entity_id="road:generated:001",
        geometry=box(0, 0, 2, 1),
    )
    building_result = _result(
        code="buildings.preferred_orientation",
        severity=ConstraintSeverity.SOFT,
        scope=ConstraintScope.BUILDING,
        entity_id="building:generated:002",
        geometry=box(10, 10, 12, 12),
    )

    aggregate = aggregate_validation_reports(
        (
            ValidationReport(results=(road_result,)),
            ValidationReport(results=(building_result,)),
        )
    )

    assert aggregate.results[0] is road_result
    assert aggregate.results[1] is building_result
    assert aggregate.hard_failures == (road_result,)
    assert aggregate.soft_violations == (building_result,)
    assert aggregate.is_valid is False


def test_cross_stage_aggregation_is_lossless_and_does_not_deduplicate() -> None:
    repeated = ConstraintResult(
        code="shared.constraint",
        severity=ConstraintSeverity.SOFT,
        scope=ConstraintScope.TERRITORY,
        passed=False,
        message="same code can be emitted by separate stage evaluations",
    )

    aggregate = aggregate_validation_reports(
        (
            ValidationReport(results=(repeated,)),
            ValidationReport(results=(repeated,)),
        )
    )

    assert aggregate.results == (repeated, repeated)


def test_cross_stage_aggregation_requires_immutable_reports() -> None:
    with pytest.raises(ConstraintContractError, match="immutable tuple"):
        aggregate_validation_reports([])

    with pytest.raises(ConstraintContractError, match="ValidationReport values"):
        aggregate_validation_reports((object(),))


def test_validation_report_serialization_is_deterministic_and_versioned() -> None:
    result = _result(
        code="buildings.minimum_gap",
        severity=ConstraintSeverity.HARD,
        scope=ConstraintScope.BUILDING,
        entity_id="building:generated:003",
        geometry=box(0, 0, 4, 4),
    )
    report = ValidationReport(results=(result,))

    first = serialize_validation_report(report)
    second = serialize_validation_report(report)

    assert first == second
    decoded = json.loads(first)
    assert decoded["schema_version"] == VALIDATION_REPORT_SCHEMA_VERSION
    assert decoded["results"][0]["entity_ref"] == {
        "entity_id": "building:generated:003"
    }
    assert decoded["results"][0]["problem_geometry"]["working_srid"] == 32637
    assert decoded["results"][0]["problem_geometry"]["wkb_hex"] == to_wkb(
        result.problem_geometry.geometry,
        hex=True,
        byte_order=1,
        include_srid=False,
    )


def test_validation_report_codec_round_trip_preserves_invalid_problem_geometry() -> None:
    bow_tie = Polygon([(0, 0), (2, 2), (0, 2), (2, 0), (0, 0)])
    assert bow_tie.is_valid is False

    source = ValidationReport(
        results=(
            _result(
                code="buildings.geometry_validity",
                severity=ConstraintSeverity.HARD,
                scope=ConstraintScope.BUILDING,
                entity_id="building:generated:004",
                geometry=bow_tie,
            ),
        )
    )

    restored = deserialize_validation_report(serialize_validation_report(source))
    restored_result = restored.results[0]

    assert restored_result.code == source.results[0].code
    assert restored_result.entity_ref == source.results[0].entity_ref
    assert restored_result.problem_geometry is not None
    assert source.results[0].problem_geometry is not None
    assert restored_result.problem_geometry.working_srid == 32637
    assert to_wkb(
        restored_result.problem_geometry.geometry,
        hex=True,
        byte_order=1,
        include_srid=False,
    ) == to_wkb(
        source.results[0].problem_geometry.geometry,
        hex=True,
        byte_order=1,
        include_srid=False,
    )
    assert restored.is_valid is False


def test_validation_report_codec_rejects_unknown_version_and_shape_drift() -> None:
    with pytest.raises(ValidationReportCodecError, match="schema_version"):
        deserialize_validation_report(
            b'{"results":[],"schema_version":2}'
        )

    with pytest.raises(ValidationReportCodecError, match="exactly"):
        deserialize_validation_report(
            b'{"extra":true,"results":[],"schema_version":1}'
        )
