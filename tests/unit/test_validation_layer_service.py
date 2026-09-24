import json
import uuid
from datetime import UTC, datetime

import pytest
from pyproj import Transformer
from shapely.geometry import Polygon, box
from shapely.ops import transform as shapely_transform

from backend.app.application.validation_layers import (
    ValidationLayerDataError,
    ValidationLayerQueryError,
    ValidationLayerQueryService,
    ValidationRunRecord,
)
from core.urban_generator.domain import (
    ConstraintEntityRef,
    ConstraintProblemGeometry,
    ConstraintResult,
    ConstraintScope,
    ConstraintSeverity,
    SoftPenaltyMetadata,
    ValidationReport,
    serialize_validation_report,
)

WORKING_SRID = 3857
_TO_WORKING = Transformer.from_crs(4326, WORKING_SRID, always_xy=True)


def _projected(geometry):
    return shapely_transform(_TO_WORKING.transform, geometry)


def _payload(report: ValidationReport) -> dict[str, object]:
    decoded = json.loads(serialize_validation_report(report))
    assert isinstance(decoded, dict)
    return decoded


def _report(
    *,
    geometry_srid: int = WORKING_SRID,
) -> ValidationReport:
    hard = ConstraintResult(
        code="roads.forbidden_crossing",
        severity=ConstraintSeverity.HARD,
        scope=ConstraintScope.ROAD,
        passed=False,
        message="Generated road crosses a protected area",
        entity_ref=ConstraintEntityRef(entity_id="road:generated:1"),
        problem_geometry=ConstraintProblemGeometry(
            geometry=_projected(box(-0.13, 51.50, -0.11, 51.52)),
            working_srid=geometry_srid,
        ),
    )
    soft = ConstraintResult(
        code="buildings.orientation",
        severity=ConstraintSeverity.SOFT,
        scope=ConstraintScope.BUILDING,
        passed=False,
        message="Preferred orientation is not satisfied",
        entity_ref=ConstraintEntityRef(entity_id="building:generated:2"),
        soft_penalty=SoftPenaltyMetadata(
            raw_penalty=0.25,
            weight=2.0,
        ),
    )
    passed = ConstraintResult(
        code="territory.coverage",
        severity=ConstraintSeverity.HARD,
        scope=ConstraintScope.TERRITORY,
        passed=True,
        message="Coverage is within bounds",
    )
    return ValidationReport(results=(hard, soft, passed))


class FakeValidationRepository:
    def __init__(self, report: ValidationReport | None = None) -> None:
        self.project_id = uuid.uuid4()
        self.run_id = uuid.uuid4()
        self.report = report or _report()

    def project_exists(self, *, project_id: uuid.UUID) -> bool:
        return project_id == self.project_id

    def list_validation_runs(
        self,
        *,
        project_id: uuid.UUID,
        limit: int,
    ) -> list[ValidationRunRecord]:
        if project_id != self.project_id:
            return []
        return [self._record()][:limit]

    def get_validation_run(
        self,
        *,
        project_id: uuid.UUID,
        run_id: uuid.UUID,
    ) -> ValidationRunRecord | None:
        if project_id != self.project_id or run_id != self.run_id:
            return None
        return self._record()

    def _record(self) -> ValidationRunRecord:
        return ValidationRunRecord(
            id=self.run_id,
            project_id=self.project_id,
            status="succeeded",
            mode="EXPANSION",
            seed=42,
            working_srid=WORKING_SRID,
            validation_json=_payload(self.report),
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
            finished_at=datetime(2026, 1, 1, 0, 1, tzinfo=UTC),
        )


def test_validation_layer_summary_and_details_use_canonical_failures() -> None:
    repository = FakeValidationRepository()
    service = ValidationLayerQueryService(repository)

    runs = service.list_runs(project_id=repository.project_id)
    summary = runs.runs[0]
    assert summary.violation_count == 2
    assert summary.hard_violation_count == 1
    assert summary.soft_violation_count == 1
    assert summary.spatial_violation_count == 1

    details = service.list_violations(
        project_id=repository.project_id,
        run_id=repository.run_id,
        limit=1,
    )
    assert details.total == 2
    assert details.truncated is True
    assert details.violations[0].violation_index == 0
    assert details.violations[0].entity_id == "road:generated:1"

    second_page = service.list_violations(
        project_id=repository.project_id,
        run_id=repository.run_id,
        offset=1,
        limit=10,
    )
    soft = second_page.violations[0]
    assert soft.violation_index == 1
    assert soft.soft_penalty is not None
    assert soft.soft_penalty.weighted_penalty == pytest.approx(0.5)


def test_geojson_uses_bbox_and_preserves_violation_identity() -> None:
    repository = FakeValidationRepository()
    service = ValidationLayerQueryService(repository)

    london = service.get_geojson(
        project_id=repository.project_id,
        run_id=repository.run_id,
        bbox_text="-0.2,51.45,0.0,51.6",
        limit=10,
    )
    assert london.matching_count == 1
    assert london.truncated is False
    feature = london.features[0]
    assert feature.id == f"{repository.run_id}:0"
    assert feature.geometry["type"] == "Polygon"
    assert feature.properties["severity"] == "HARD"
    assert feature.properties["code"] == "roads.forbidden_crossing"
    assert feature.properties["entity_id"] == "road:generated:1"

    paris = service.get_geojson(
        project_id=repository.project_id,
        run_id=repository.run_id,
        bbox_text="2.2,48.8,2.5,49.0",
        limit=10,
    )
    assert paris.matching_count == 0
    assert paris.features == ()


def test_invalid_problem_geometry_is_kept_as_validation_evidence() -> None:
    bow_tie_wgs84 = Polygon(
        [
            (-0.13, 51.50),
            (-0.11, 51.52),
            (-0.13, 51.52),
            (-0.11, 51.50),
            (-0.13, 51.50),
        ]
    )
    bow_tie = _projected(bow_tie_wgs84)
    assert bow_tie.is_valid is False
    report = ValidationReport(
        results=(
            ConstraintResult(
                code="buildings.geometry_validity",
                severity=ConstraintSeverity.HARD,
                scope=ConstraintScope.BUILDING,
                passed=False,
                message="Building geometry is invalid",
                problem_geometry=ConstraintProblemGeometry(
                    geometry=bow_tie,
                    working_srid=WORKING_SRID,
                ),
            ),
        )
    )
    repository = FakeValidationRepository(report)
    result = ValidationLayerQueryService(repository).get_geojson(
        project_id=repository.project_id,
        run_id=repository.run_id,
        bbox_text="-0.2,51.45,0.0,51.6",
    )

    assert result.matching_count == 1
    assert result.features[0].geometry["type"] == "Polygon"


def test_validation_layer_rejects_bad_bbox_and_srid_drift() -> None:
    repository = FakeValidationRepository()
    service = ValidationLayerQueryService(repository)
    with pytest.raises(ValidationLayerQueryError, match="bbox"):
        service.get_geojson(
            project_id=repository.project_id,
            run_id=repository.run_id,
            bbox_text="bad",
        )

    drifted = FakeValidationRepository(_report(geometry_srid=32637))
    with pytest.raises(ValidationLayerDataError, match="working SRID"):
        ValidationLayerQueryService(drifted).list_violations(
            project_id=drifted.project_id,
            run_id=drifted.run_id,
        )
