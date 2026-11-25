from __future__ import annotations

import json
import math
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol, cast

from pyproj import Transformer
from shapely.geometry import mapping
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as shapely_transform

from backend.app.application.source_layers import (
    GEOJSON_CRS,
    SourceLayerBbox,
    SourceLayerQueryError,
)
from core.urban_generator.domain import (
    ConstraintResult,
    ConstraintScope,
    ConstraintSeverity,
    ValidationReport,
    ValidationReportCodecError,
    deserialize_validation_report,
)

DEFAULT_VALIDATION_RUN_LIMIT = 50
MAX_VALIDATION_RUN_LIMIT = 100
DEFAULT_VIOLATION_LIMIT = 200
MAX_VIOLATION_LIMIT = 1000


class ValidationLayerQueryError(ValueError):
    """Raised when a validation-layer request violates its public contract."""


class ValidationLayerProjectNotFoundError(LookupError):
    def __init__(self, project_id: uuid.UUID) -> None:
        self.project_id = project_id
        super().__init__(f"project {project_id} was not found")


class ValidationLayerRunNotFoundError(LookupError):
    def __init__(self, project_id: uuid.UUID, run_id: uuid.UUID) -> None:
        self.project_id = project_id
        self.run_id = run_id
        super().__init__(
            f"validation run {run_id} was not found in project {project_id}"
        )


class ValidationLayerDataError(RuntimeError):
    """Raised when persisted validation data violates the canonical contract."""


@dataclass(frozen=True, slots=True)
class ValidationRunRecord:
    id: uuid.UUID
    project_id: uuid.UUID
    status: str
    mode: str
    seed: int
    working_srid: int
    validation_json: dict[str, Any]
    created_at: datetime
    finished_at: datetime | None


@dataclass(frozen=True, slots=True)
class ValidationRunSummary:
    id: uuid.UUID
    project_id: uuid.UUID
    status: str
    mode: str
    seed: int
    working_srid: int
    violation_count: int
    hard_violation_count: int
    soft_violation_count: int
    spatial_violation_count: int
    created_at: datetime
    finished_at: datetime | None


@dataclass(frozen=True, slots=True)
class ValidationRunListResult:
    project_id: uuid.UUID
    limit: int
    truncated: bool
    runs: tuple[ValidationRunSummary, ...]


@dataclass(frozen=True, slots=True)
class ViolationSoftPenalty:
    raw_penalty: float
    weight: float
    weighted_penalty: float
    schema_version: int


@dataclass(frozen=True, slots=True)
class ViolationDetail:
    violation_index: int
    code: str
    severity: ConstraintSeverity
    scope: ConstraintScope
    message: str
    entity_id: str | None
    has_problem_geometry: bool
    soft_penalty: ViolationSoftPenalty | None


@dataclass(frozen=True, slots=True)
class ViolationListResult:
    project_id: uuid.UUID
    run_id: uuid.UUID
    working_srid: int
    offset: int
    limit: int
    total: int
    truncated: bool
    violations: tuple[ViolationDetail, ...]


@dataclass(frozen=True, slots=True)
class ViolationFeature:
    id: str
    geometry: dict[str, object]
    properties: dict[str, object]
    type: Literal["Feature"] = field(init=False, default="Feature")


@dataclass(frozen=True, slots=True)
class ViolationGeoJSONResult:
    project_id: uuid.UUID
    run_id: uuid.UUID
    query_bbox: tuple[float, float, float, float]
    geojson_crs: Literal["EPSG:4326"]
    working_srid: int
    limit: int
    matching_count: int
    truncated: bool
    features: tuple[ViolationFeature, ...]
    type: Literal["FeatureCollection"] = field(
        init=False,
        default="FeatureCollection",
    )


class ValidationLayerQueryRepository(Protocol):
    def project_exists(self, *, project_id: uuid.UUID) -> bool:
        ...

    def list_validation_runs(
        self,
        *,
        project_id: uuid.UUID,
        limit: int,
    ) -> list[ValidationRunRecord]:
        ...

    def get_validation_run(
        self,
        *,
        project_id: uuid.UUID,
        run_id: uuid.UUID,
    ) -> ValidationRunRecord | None:
        ...


class ValidationLayerQueryService:
    """Project canonical run validation into bounded HTTP/UI read models."""

    def __init__(self, repository: ValidationLayerQueryRepository) -> None:
        self._repository = repository

    def list_runs(
        self,
        *,
        project_id: uuid.UUID,
        limit: int = DEFAULT_VALIDATION_RUN_LIMIT,
    ) -> ValidationRunListResult:
        _require_limit(
            limit,
            maximum=MAX_VALIDATION_RUN_LIMIT,
            field_name="run limit",
        )
        if not self._repository.project_exists(project_id=project_id):
            raise ValidationLayerProjectNotFoundError(project_id)

        records = self._repository.list_validation_runs(
            project_id=project_id,
            limit=limit + 1,
        )
        truncated = len(records) > limit
        summaries = tuple(
            self._summary(record)
            for record in records[:limit]
        )
        return ValidationRunListResult(
            project_id=project_id,
            limit=limit,
            truncated=truncated,
            runs=summaries,
        )

    def list_violations(
        self,
        *,
        project_id: uuid.UUID,
        run_id: uuid.UUID,
        offset: int = 0,
        limit: int = DEFAULT_VIOLATION_LIMIT,
    ) -> ViolationListResult:
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValidationLayerQueryError(
                "offset must be a non-negative integer"
            )
        _require_limit(
            limit,
            maximum=MAX_VIOLATION_LIMIT,
            field_name="limit",
        )
        record, failures = self._run_failures(
            project_id=project_id,
            run_id=run_id,
        )
        total = len(failures)
        selected = failures[offset : offset + limit]
        return ViolationListResult(
            project_id=project_id,
            run_id=run_id,
            working_srid=record.working_srid,
            offset=offset,
            limit=limit,
            total=total,
            truncated=offset + len(selected) < total,
            violations=tuple(
                _detail(index, result) for index, result in selected
            ),
        )

    def get_geojson(
        self,
        *,
        project_id: uuid.UUID,
        run_id: uuid.UUID,
        bbox_text: str,
        limit: int = DEFAULT_VIOLATION_LIMIT,
    ) -> ViolationGeoJSONResult:
        _require_limit(
            limit,
            maximum=MAX_VIOLATION_LIMIT,
            field_name="limit",
        )
        try:
            bbox = SourceLayerBbox.parse(bbox_text)
        except SourceLayerQueryError as exc:
            raise ValidationLayerQueryError(str(exc)) from exc

        record, failures = self._run_failures(
            project_id=project_id,
            run_id=run_id,
        )
        transformer = Transformer.from_crs(
            record.working_srid,
            4326,
            always_xy=True,
        )
        matching: list[ViolationFeature] = []
        matching_count = 0
        for index, result in failures:
            problem = result.problem_geometry
            if problem is None:
                continue
            geometry = shapely_transform(
                transformer.transform,
                problem.geometry,
            )
            if not _bounds_overlap(geometry, bbox):
                continue
            matching_count += 1
            if len(matching) >= limit:
                continue
            matching.append(
                ViolationFeature(
                    id=f"{run_id}:{index}",
                    geometry=_geometry_mapping(geometry),
                    properties=_feature_properties(index, result),
                )
            )

        return ViolationGeoJSONResult(
            project_id=project_id,
            run_id=run_id,
            query_bbox=bbox.tuple,
            geojson_crs=GEOJSON_CRS,
            working_srid=record.working_srid,
            limit=limit,
            matching_count=matching_count,
            truncated=matching_count > limit,
            features=tuple(matching),
        )

    def _summary(self, record: ValidationRunRecord) -> ValidationRunSummary:
        report = _decode_report(record)
        failures = report.failures
        return ValidationRunSummary(
            id=record.id,
            project_id=record.project_id,
            status=record.status,
            mode=record.mode,
            seed=record.seed,
            working_srid=record.working_srid,
            violation_count=len(failures),
            hard_violation_count=len(report.hard_failures),
            soft_violation_count=len(report.soft_violations),
            spatial_violation_count=sum(
                result.problem_geometry is not None for result in failures
            ),
            created_at=record.created_at,
            finished_at=record.finished_at,
        )

    def _run_failures(
        self,
        *,
        project_id: uuid.UUID,
        run_id: uuid.UUID,
    ) -> tuple[
        ValidationRunRecord,
        tuple[tuple[int, ConstraintResult], ...],
    ]:
        record = self._repository.get_validation_run(
            project_id=project_id,
            run_id=run_id,
        )
        if record is None:
            raise ValidationLayerRunNotFoundError(project_id, run_id)
        report = _decode_report(record)
        failures: list[tuple[int, ConstraintResult]] = []
        for index, result in enumerate(report.results):
            problem = result.problem_geometry
            if (
                problem is not None
                and problem.working_srid != record.working_srid
            ):
                raise ValidationLayerDataError(
                    "persisted problem geometry working SRID does not match "
                    f"run working_srid={record.working_srid}"
                )
            if result.failed:
                failures.append((index, result))
        return record, tuple(failures)


def _decode_report(record: ValidationRunRecord) -> ValidationReport:
    try:
        payload = json.dumps(
            record.validation_json,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return deserialize_validation_report(payload)
    except (
        TypeError,
        ValueError,
        ValidationReportCodecError,
    ) as exc:
        raise ValidationLayerDataError(
            f"run {record.id} has invalid persisted validation data"
        ) from exc


def _detail(
    index: int,
    result: ConstraintResult,
) -> ViolationDetail:
    penalty = result.soft_penalty
    return ViolationDetail(
        violation_index=index,
        code=result.code,
        severity=result.severity,
        scope=result.scope,
        message=result.message,
        entity_id=(
            result.entity_ref.entity_id
            if result.entity_ref is not None
            else None
        ),
        has_problem_geometry=result.problem_geometry is not None,
        soft_penalty=(
            ViolationSoftPenalty(
                raw_penalty=penalty.raw_penalty,
                weight=penalty.weight,
                weighted_penalty=penalty.weighted_penalty,
                schema_version=penalty.schema_version,
            )
            if penalty is not None
            else None
        ),
    )


def _feature_properties(
    index: int,
    result: ConstraintResult,
) -> dict[str, object]:
    penalty = result.soft_penalty
    return {
        "violation_index": index,
        "code": result.code,
        "severity": result.severity.value,
        "scope": result.scope.value,
        "message": result.message,
        "entity_id": (
            result.entity_ref.entity_id
            if result.entity_ref is not None
            else None
        ),
        "soft_penalty_raw": (
            penalty.raw_penalty if penalty is not None else None
        ),
        "soft_penalty_weight": (
            penalty.weight if penalty is not None else None
        ),
        "soft_penalty_weighted": (
            penalty.weighted_penalty if penalty is not None else None
        ),
    }


def _geometry_mapping(geometry: BaseGeometry) -> dict[str, object]:
    raw = mapping(geometry)
    return cast(dict[str, object], raw)


def _bounds_overlap(
    geometry: BaseGeometry,
    bbox: SourceLayerBbox,
) -> bool:
    bounds = geometry.bounds
    if len(bounds) != 4 or not all(math.isfinite(value) for value in bounds):
        raise ValidationLayerDataError(
            "problem geometry cannot be represented in EPSG:4326"
        )
    west, south, east, north = bounds
    return not (
        east < bbox.west
        or west > bbox.east
        or north < bbox.south
        or south > bbox.north
    )


def _require_limit(
    value: int,
    *,
    maximum: int,
    field_name: str,
) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationLayerQueryError(f"{field_name} must be an integer")
    if value < 1 or value > maximum:
        raise ValidationLayerQueryError(
            f"{field_name} must be between 1 and {maximum}"
        )
