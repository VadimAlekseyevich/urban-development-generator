from __future__ import annotations

import json
from typing import cast

from shapely import from_wkb, to_wkb
from shapely.geometry.base import BaseGeometry

from core.urban_generator.domain.constraints import (
    ConstraintContractError,
    ConstraintEntityRef,
    ConstraintProblemGeometry,
    ConstraintResult,
    ConstraintScope,
    ConstraintSeverity,
    SoftPenaltyMetadata,
    ValidationReport,
)

VALIDATION_REPORT_SCHEMA_VERSION = 2
_SUPPORTED_VALIDATION_REPORT_SCHEMA_VERSIONS = frozenset({1, 2})


class ValidationReportCodecError(ConstraintContractError):
    """Raised when a serialized ValidationReport violates the canonical v1 codec contract."""


def aggregate_validation_reports(
    reports: tuple[ValidationReport, ...],
) -> ValidationReport:
    """Combine stage reports in caller-supplied canonical stage order.

    Aggregation is intentionally lossless: results are neither reordered nor deduplicated.
    """

    if not isinstance(reports, tuple):
        raise ConstraintContractError("validation reports must be an immutable tuple")
    if any(not isinstance(report, ValidationReport) for report in reports):
        raise ConstraintContractError(
            "validation reports must contain only ValidationReport values"
        )

    return ValidationReport(
        results=tuple(
            result
            for report in reports
            for result in report.results
        )
    )


def serialize_validation_report(report: ValidationReport) -> bytes:
    """Serialize a ValidationReport to deterministic UTF-8 JSON bytes."""

    if not isinstance(report, ValidationReport):
        raise ValidationReportCodecError(
            "serialized value must be a ValidationReport"
        )

    payload: dict[str, object] = {
        "results": [_serialize_result(result) for result in report.results],
        "schema_version": VALIDATION_REPORT_SCHEMA_VERSION,
    }
    return json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def deserialize_validation_report(payload: bytes) -> ValidationReport:
    """Restore a ValidationReport from the strict canonical v1 JSON representation."""

    if not isinstance(payload, bytes):
        raise ValidationReportCodecError("validation report payload must be bytes")

    try:
        decoded: object = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationReportCodecError(
            "validation report payload must be valid UTF-8 JSON"
        ) from exc

    root = _require_mapping(decoded, field_name="validation report payload")
    _require_exact_keys(root, {"results", "schema_version"}, field_name="payload")

    version = root["schema_version"]
    if (
        not isinstance(version, int)
        or isinstance(version, bool)
        or version not in _SUPPORTED_VALIDATION_REPORT_SCHEMA_VERSIONS
    ):
        raise ValidationReportCodecError(
            f"unsupported validation report schema_version: {version!r}"
        )

    raw_results = root["results"]
    if not isinstance(raw_results, list):
        raise ValidationReportCodecError(
            "validation report results must be a JSON array"
        )

    return ValidationReport(
        results=tuple(
            _deserialize_result(item, schema_version=version)
            for item in raw_results
        )
    )


def _serialize_result(result: ConstraintResult) -> dict[str, object]:
    entity_ref: dict[str, object] | None = None
    if result.entity_ref is not None:
        entity_ref = {"entity_id": result.entity_ref.entity_id}

    problem_geometry: dict[str, object] | None = None
    if result.problem_geometry is not None:
        wkb_hex = to_wkb(
            result.problem_geometry.geometry,
            hex=True,
            byte_order=1,
            include_srid=False,
        )
        problem_geometry = {
            "working_srid": result.problem_geometry.working_srid,
            "wkb_hex": cast(str, wkb_hex),
        }

    soft_penalty: dict[str, object] | None = None
    if result.soft_penalty is not None:
        soft_penalty = {
            "raw_penalty": result.soft_penalty.raw_penalty,
            "schema_version": result.soft_penalty.schema_version,
            "weight": result.soft_penalty.weight,
        }

    return {
        "code": result.code,
        "entity_ref": entity_ref,
        "message": result.message,
        "passed": result.passed,
        "problem_geometry": problem_geometry,
        "scope": result.scope.value,
        "severity": result.severity.value,
        "soft_penalty": soft_penalty,
    }


def _deserialize_result(
    value: object,
    *,
    schema_version: int,
) -> ConstraintResult:
    item = _require_mapping(value, field_name="constraint result")
    expected_keys = {
        "code",
        "entity_ref",
        "message",
        "passed",
        "problem_geometry",
        "scope",
        "severity",
    }
    if schema_version >= 2:
        expected_keys.add("soft_penalty")
    _require_exact_keys(
        item,
        expected_keys,
        field_name="constraint result",
    )

    entity_ref = _deserialize_entity_ref(item["entity_ref"])
    problem_geometry = _deserialize_problem_geometry(item["problem_geometry"])
    soft_penalty = (
        _deserialize_soft_penalty(item["soft_penalty"])
        if schema_version >= 2
        else None
    )

    try:
        severity = ConstraintSeverity(cast(str, item["severity"]))
        scope = ConstraintScope(cast(str, item["scope"]))
        return ConstraintResult(
            code=cast(str, item["code"]),
            severity=severity,
            scope=scope,
            passed=cast(bool, item["passed"]),
            message=cast(str, item["message"]),
            entity_ref=entity_ref,
            problem_geometry=problem_geometry,
            soft_penalty=soft_penalty,
        )
    except (ConstraintContractError, TypeError, ValueError) as exc:
        raise ValidationReportCodecError(
            "serialized constraint result violates the domain contract"
        ) from exc


def _deserialize_soft_penalty(value: object) -> SoftPenaltyMetadata | None:
    if value is None:
        return None

    item = _require_mapping(value, field_name="constraint soft_penalty")
    _require_exact_keys(
        item,
        {"raw_penalty", "schema_version", "weight"},
        field_name="constraint soft_penalty",
    )
    try:
        return SoftPenaltyMetadata(
            raw_penalty=cast(float, item["raw_penalty"]),
            weight=cast(float, item["weight"]),
            schema_version=cast(int, item["schema_version"]),
        )
    except (ConstraintContractError, TypeError, ValueError) as exc:
        raise ValidationReportCodecError(
            "serialized constraint soft_penalty violates the domain contract"
        ) from exc


def _deserialize_entity_ref(value: object) -> ConstraintEntityRef | None:
    if value is None:
        return None

    item = _require_mapping(value, field_name="constraint entity_ref")
    _require_exact_keys(item, {"entity_id"}, field_name="constraint entity_ref")
    try:
        return ConstraintEntityRef(entity_id=cast(str, item["entity_id"]))
    except ConstraintContractError as exc:
        raise ValidationReportCodecError(
            "serialized constraint entity_ref violates the domain contract"
        ) from exc


def _deserialize_problem_geometry(
    value: object,
) -> ConstraintProblemGeometry | None:
    if value is None:
        return None

    item = _require_mapping(value, field_name="constraint problem_geometry")
    _require_exact_keys(
        item,
        {"working_srid", "wkb_hex"},
        field_name="constraint problem_geometry",
    )

    wkb_hex = item["wkb_hex"]
    if not isinstance(wkb_hex, str) or not wkb_hex:
        raise ValidationReportCodecError(
            "constraint problem_geometry wkb_hex must be a non-empty string"
        )

    try:
        geometry = from_wkb(bytes.fromhex(wkb_hex))
    except (TypeError, ValueError) as exc:
        raise ValidationReportCodecError(
            "constraint problem_geometry wkb_hex must contain valid WKB"
        ) from exc
    if not isinstance(geometry, BaseGeometry):
        raise ValidationReportCodecError(
            "constraint problem_geometry WKB must decode to one geometry"
        )

    try:
        return ConstraintProblemGeometry(
            geometry=geometry,
            working_srid=cast(int, item["working_srid"]),
        )
    except (ConstraintContractError, TypeError, ValueError) as exc:
        raise ValidationReportCodecError(
            "serialized constraint problem_geometry violates the domain contract"
        ) from exc


def _require_mapping(value: object, *, field_name: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(
        not isinstance(key, str) for key in value
    ):
        raise ValidationReportCodecError(f"{field_name} must be a JSON object")
    return cast(dict[str, object], value)


def _require_exact_keys(
    value: dict[str, object],
    expected: set[str],
    *,
    field_name: str,
) -> None:
    if set(value) != expected:
        raise ValidationReportCodecError(
            f"{field_name} must contain exactly {sorted(expected)!r}"
        )
