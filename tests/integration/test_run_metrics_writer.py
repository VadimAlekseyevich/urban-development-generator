import uuid

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from backend.app.db.run_metrics_writer import (
    RUN_EVALUATION_METRICS_KEY,
    RunMetricsImmutableError,
    SqlAlchemyRunMetricsWriter,
)
from backend.app.db.session import engine
from backend.app.models.generation_run import GenerationRun
from backend.app.models.project import Project
from core.urban_generator.domain.benchmarking import MetricDirection, RawMetricId
from core.urban_generator.metrics.normalization import (
    MetricNormalizationPolicy,
    MetricNormalizationProfile,
    NormalizationClampPolicy,
    NormalizationMissingPolicy,
)
from core.urban_generator.metrics.score import (
    CompositeScoreConfig,
    CompositeScoreMetricWeight,
    CompositeScoreRawMetric,
    build_composite_score,
)

WORKING_SRID = 3857


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


def _create_run(*, status: str = "running") -> uuid.UUID:
    with Session(engine, expire_on_commit=False) as session:
        with session.begin():
            project = Project(
                name="Composite score persistence",
                working_srid=WORKING_SRID,
                boundary_metadata={},
            )
            session.add(project)
            session.flush()
            run = GenerationRun(
                project_id=project.id,
                status=status,
                mode="EXPANSION",
                seed=42,
                working_srid=WORKING_SRID,
                config_json={},
                config_schema_version="1",
                commit_sha="a" * 40 if status == "succeeded" else None,
                metrics_json={"demography": {"population": 100}},
            )
            session.add(run)
            session.flush()
            return run.id


def _result():
    coverage_id = RawMetricId.INFRASTRUCTURE_POPULATION_COVERAGE_RATIO
    circuity_id = RawMetricId.ROADS_CIRCUITY
    profile = MetricNormalizationProfile(
        profile_id="evaluation-default",
        version="1",
        policies=(
            MetricNormalizationPolicy(
                metric_id=coverage_id,
                direction=MetricDirection.HIGHER_IS_BETTER,
                lower_bound=0.0,
                upper_bound=1.0,
                clamp_policy=NormalizationClampPolicy.CLAMP,
                missing_policy=NormalizationMissingPolicy.REJECT,
                version="coverage-v1",
            ),
            MetricNormalizationPolicy(
                metric_id=circuity_id,
                direction=MetricDirection.LOWER_IS_BETTER,
                lower_bound=1.0,
                upper_bound=2.0,
                clamp_policy=NormalizationClampPolicy.CLAMP,
                missing_policy=NormalizationMissingPolicy.AS_WORST,
                version="circuity-v1",
            ),
        ),
    )
    config = CompositeScoreConfig(
        config_id="score-default",
        version="1",
        weights=(
            CompositeScoreMetricWeight(coverage_id, 3.0),
            CompositeScoreMetricWeight(circuity_id, 1.0),
        ),
    )
    return build_composite_score(
        (
            CompositeScoreRawMetric(coverage_id, 0.8),
            CompositeScoreRawMetric(circuity_id, 1.4),
        ),
        normalization_profile=profile,
        config=config,
    )


def test_writer_persists_versioned_score_envelope_without_losing_existing_metrics() -> None:
    run_id = _create_run()
    result = _result()

    write = SqlAlchemyRunMetricsWriter().replace(run_id=run_id, result=result)

    assert write.metric_count == 2
    assert write.composite_score == pytest.approx(result.score)

    with Session(engine) as session:
        run = session.scalar(select(GenerationRun).where(GenerationRun.id == run_id))
        assert run is not None
        assert run.metrics_json is not None
        assert run.metrics_json["demography"]["population"] == 100

        payload = run.metrics_json[RUN_EVALUATION_METRICS_KEY]
        assert payload["schema_version"] == "1"
        assert payload["composite_score"] == pytest.approx(result.score)
        assert payload["score_config"] == {"id": "score-default", "version": "1"}
        assert payload["normalization_profile"] == {
            "id": "evaluation-default",
            "version": "1",
        }
        assert [item["metric_id"] for item in payload["metrics"]] == [
            RawMetricId.INFRASTRUCTURE_POPULATION_COVERAGE_RATIO.value,
            RawMetricId.ROADS_CIRCUITY.value,
        ]
        assert payload["metrics"][0]["raw_value"] == pytest.approx(0.8)
        assert payload["metrics"][0]["configured_weight"] == pytest.approx(3.0)
        assert payload["metrics"][0]["contribution"] == pytest.approx(0.6)


def test_writer_retry_replaces_only_evaluation_envelope_deterministically() -> None:
    run_id = _create_run()
    result = _result()
    writer = SqlAlchemyRunMetricsWriter()

    first = writer.replace(run_id=run_id, result=result)
    second = writer.replace(run_id=run_id, result=result)

    assert second == first
    with Session(engine) as session:
        run = session.get(GenerationRun, run_id)
        assert run is not None
        assert run.metrics_json is not None
        assert set(run.metrics_json) == {"demography", RUN_EVALUATION_METRICS_KEY}
        assert run.metrics_json[RUN_EVALUATION_METRICS_KEY]["composite_score"] == (
            pytest.approx(result.score)
        )


def test_writer_rejects_successful_run_without_mutating_metrics() -> None:
    run_id = _create_run(status="succeeded")

    with pytest.raises(RunMetricsImmutableError, match="immutable"):
        SqlAlchemyRunMetricsWriter().replace(run_id=run_id, result=_result())

    with Session(engine) as session:
        run = session.get(GenerationRun, run_id)
        assert run is not None
        assert run.metrics_json == {"demography": {"population": 100}}
