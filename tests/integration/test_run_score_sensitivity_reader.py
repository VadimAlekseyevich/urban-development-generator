import uuid

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.app.db.run_score_sensitivity_reader import (
    RunScoreSensitivityPersistenceError,
    SqlAlchemyRunScoreSensitivityReader,
)
from backend.app.db.session import engine
from backend.app.models.generation_run import GenerationRun
from backend.app.models.project import Project
from core.urban_generator.domain.benchmarking import RawMetricId

WORKING_SRID = 3857
COVERAGE = RawMetricId.INFRASTRUCTURE_POPULATION_COVERAGE_RATIO
CIRCUITY = RawMetricId.ROADS_CIRCUITY


def _migrate_to_head() -> None:
    command.upgrade(Config("alembic.ini"), "head")


def _truncate_state() -> None:
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE projects, artifacts CASCADE"))


@pytest.fixture(scope="module", autouse=True)
def migrated_database() -> None:
    _migrate_to_head()


@pytest.fixture(autouse=True)
def clean_database(migrated_database: None) -> None:
    _truncate_state()
    yield
    _truncate_state()


def _evaluation(
    *,
    coverage: float,
    circuity: float,
    profile_version: str = "1",
    score_config_id: str = "score-default",
) -> dict[str, object]:
    return {
        "schema_version": "1",
        "composite_score": (coverage + circuity) / 2.0,
        "score_config": {
            "id": score_config_id,
            "version": "1",
        },
        "normalization_profile": {
            "id": "evaluation-default",
            "version": profile_version,
        },
        "metrics": [
            {
                "metric_id": COVERAGE.value,
                "raw_value": coverage,
                "normalized_value": coverage,
                "normalization_policy_version": "coverage-v1",
                "configured_weight": 1.0,
                "normalized_weight": 0.5,
                "contribution": coverage * 0.5,
                "was_clamped": False,
                "was_missing": False,
            },
            {
                "metric_id": CIRCUITY.value,
                "raw_value": 1.2,
                "normalized_value": circuity,
                "normalization_policy_version": "circuity-v1",
                "configured_weight": 1.0,
                "normalized_weight": 0.5,
                "contribution": circuity * 0.5,
                "was_clamped": False,
                "was_missing": False,
            },
        ],
    }


def _create_runs(
    evaluations: tuple[dict[str, object], ...],
) -> tuple[uuid.UUID, ...]:
    with Session(engine, expire_on_commit=False) as session:
        with session.begin():
            project = Project(
                name="Score sensitivity persistence",
                working_srid=WORKING_SRID,
                boundary_metadata={},
            )
            session.add(project)
            session.flush()

            run_ids: list[uuid.UUID] = []
            for index, evaluation in enumerate(evaluations):
                run = GenerationRun(
                    project_id=project.id,
                    status="succeeded",
                    mode="EXPANSION",
                    seed=index + 1,
                    working_srid=WORKING_SRID,
                    config_json={},
                    config_schema_version="1",
                    commit_sha="a" * 40,
                    metrics_json={
                        "demography": {"population": 100 + index},
                        "evaluation": evaluation,
                    },
                )
                session.add(run)
                session.flush()
                run_ids.append(run.id)
            return tuple(run_ids)


def test_reader_loads_bounded_persisted_score_inputs_without_gis_data() -> None:
    run_ids = _create_runs(
        (
            _evaluation(coverage=0.9, circuity=0.2),
            _evaluation(coverage=0.5, circuity=0.9),
        )
    )

    dataset = SqlAlchemyRunScoreSensitivityReader(max_runs=2).load(
        run_ids=tuple(reversed(run_ids)),
    )

    assert dataset.baseline_config.config_id == "score-default"
    assert dataset.baseline_config.metric_ids == (COVERAGE, CIRCUITY)
    assert [run.run_ref for run in dataset.runs] == [
        str(run_ids[1]),
        str(run_ids[0]),
    ]
    first = dataset.runs[0]
    assert first.normalization_profile_id == "evaluation-default"
    assert first.metrics[0].raw_value == pytest.approx(0.5)
    assert first.metrics[0].normalized_value == pytest.approx(0.5)


def test_reader_rejects_missing_evaluation_and_mixed_score_configs() -> None:
    run_ids = _create_runs(
        (
            _evaluation(coverage=0.8, circuity=0.4),
            _evaluation(
                coverage=0.7,
                circuity=0.6,
                score_config_id="different",
            ),
        )
    )

    with pytest.raises(
        RunScoreSensitivityPersistenceError,
        match="same baseline score config",
    ):
        SqlAlchemyRunScoreSensitivityReader().load(run_ids=run_ids)

    with Session(engine) as session:
        run = session.get(GenerationRun, run_ids[0])
        assert run is not None
        run.metrics_json = {"demography": {"population": 100}}
        session.commit()

    with pytest.raises(
        RunScoreSensitivityPersistenceError,
        match="no persisted evaluation envelope",
    ):
        SqlAlchemyRunScoreSensitivityReader().load(
            run_ids=(run_ids[0],),
        )


def test_reader_enforces_run_bound_and_exact_requested_ids() -> None:
    run_ids = _create_runs(
        (
            _evaluation(coverage=0.8, circuity=0.4),
            _evaluation(coverage=0.7, circuity=0.6),
        )
    )

    with pytest.raises(
        RunScoreSensitivityPersistenceError,
        match="max_runs=1",
    ):
        SqlAlchemyRunScoreSensitivityReader(max_runs=1).load(
            run_ids=run_ids,
        )

    with pytest.raises(
        RunScoreSensitivityPersistenceError,
        match="generation run not found",
    ):
        SqlAlchemyRunScoreSensitivityReader().load(
            run_ids=(uuid.uuid4(),),
        )


def test_reader_rejects_malformed_evaluation_schema() -> None:
    bad = _evaluation(coverage=0.8, circuity=0.4)
    bad["schema_version"] = "999"
    run_id = _create_runs((bad,))[0]

    with pytest.raises(
        RunScoreSensitivityPersistenceError,
        match="unsupported evaluation schema_version",
    ):
        SqlAlchemyRunScoreSensitivityReader().load(
            run_ids=(run_id,),
        )
