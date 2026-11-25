from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.app.db.session import SessionLocal
from backend.app.models.generated_entity import GeneratedBlock
from backend.app.models.generation_run import RUN_SUCCESS_STATUS, GenerationRun
from core.urban_generator.demography.aggregation import DemographicAggregationResult
from core.urban_generator.demography.metrics import (
    DemographyBlockArea,
    DemographyMetricsBuilder,
    DemographyMetricsResult,
)
from core.urban_generator.domain import DataError


class GeneratedDemographyPersistenceError(DataError):
    """Base error for S09-T10 demographic metric persistence."""


class GeneratedDemographyImmutableError(GeneratedDemographyPersistenceError):
    """Raised before mutating demographic metrics of a successful run."""


@dataclass(frozen=True, slots=True)
class GeneratedDemographyWriteResult:
    run_id: uuid.UUID
    updated_block_rows: int
    metrics: DemographyMetricsResult


class SqlAlchemyGeneratedDemographyWriter:
    """Persist S09 demographic read-models into existing run/block JSON extension points."""

    def __init__(
        self,
        *,
        session_factory: Callable[[], Session] = SessionLocal,
        max_blocks: int = 100_000,
    ) -> None:
        self._session_factory = session_factory
        self._metrics_builder = DemographyMetricsBuilder(max_blocks=max_blocks)

    def replace(
        self,
        *,
        run_id: uuid.UUID,
        aggregation: DemographicAggregationResult,
    ) -> GeneratedDemographyWriteResult:
        if not isinstance(run_id, uuid.UUID):
            raise TypeError("run_id must be UUID")
        if not isinstance(aggregation, DemographicAggregationResult):
            raise TypeError("aggregation must be DemographicAggregationResult")

        try:
            with self._session_factory() as session:
                with session.begin():
                    run = session.scalars(
                        select(GenerationRun)
                        .where(GenerationRun.id == run_id)
                        .with_for_update()
                    ).one_or_none()
                    if run is None:
                        raise GeneratedDemographyPersistenceError(
                            f"generation run not found: {run_id}"
                        )
                    if run.status == RUN_SUCCESS_STATUS:
                        raise GeneratedDemographyImmutableError(
                            "demographic metrics of a successful generation run are immutable"
                        )

                    blocks = list(
                        session.scalars(
                            select(GeneratedBlock)
                            .where(GeneratedBlock.run_id == run_id)
                            .order_by(GeneratedBlock.block_key, GeneratedBlock.id)
                            .with_for_update()
                        ).all()
                    )
                    by_key = self._validate_blocks(
                        blocks=blocks,
                        aggregation=aggregation,
                    )
                    metrics = self._metrics_builder.build(
                        aggregation,
                        block_areas=tuple(
                            DemographyBlockArea(
                                block_id=block.block_key,
                                area_m2=block.area_m2,
                            )
                            for block in blocks
                            if block.block_key is not None
                            and block.area_m2 is not None
                        ),
                    )

                    for item in metrics.blocks:
                        block = by_key[item.block_id]
                        attributes = dict(block.attributes_json or {})
                        attributes["demography"] = _block_payload(item)
                        block.attributes_json = attributes

                    run_metrics = dict(run.metrics_json or {})
                    run_metrics["demography"] = _totals_payload(metrics)
                    run.metrics_json = run_metrics

            return GeneratedDemographyWriteResult(
                run_id=run_id,
                updated_block_rows=len(metrics.blocks),
                metrics=metrics,
            )
        except GeneratedDemographyPersistenceError:
            raise
        except SQLAlchemyError as exc:
            raise GeneratedDemographyPersistenceError(
                f"unable to persist demographic metrics for run {run_id}"
            ) from exc

    @staticmethod
    def _validate_blocks(
        *,
        blocks: list[GeneratedBlock],
        aggregation: DemographicAggregationResult,
    ) -> dict[str, GeneratedBlock]:
        by_key: dict[str, GeneratedBlock] = {}
        for block in blocks:
            if block.block_key is None or not block.block_key:
                raise GeneratedDemographyPersistenceError(
                    "generated block must have block_key before demographic persistence"
                )
            if block.area_m2 is None or block.area_m2 <= 0.0:
                raise GeneratedDemographyPersistenceError(
                    f"generated block {block.block_key} must have positive area_m2"
                )
            if block.block_key in by_key:
                raise GeneratedDemographyPersistenceError(
                    f"duplicate generated block_key: {block.block_key}"
                )
            by_key[block.block_key] = block

        expected = {item.block_id for item in aggregation.blocks}
        if set(by_key) != expected:
            raise GeneratedDemographyPersistenceError(
                "generated blocks and demographic aggregation must contain identical block ids"
            )

        for item in aggregation.blocks:
            block = by_key[item.block_id]
            zone_class = (block.attributes_json or {}).get("zone_class")
            if zone_class is not None and zone_class != item.zone_class.value:
                raise GeneratedDemographyPersistenceError(
                    f"zone class mismatch for block {item.block_id}"
                )
            if block.zone_id is not None and str(block.zone_id) != item.zone_id:
                raise GeneratedDemographyPersistenceError(
                    f"zone id mismatch for block {item.block_id}"
                )
        return by_key


def _block_payload(item: Any) -> dict[str, Any]:
    return {
        "population": item.population,
        "population_density_per_km2": item.population_density_per_km2,
        "jobs_estimate": item.jobs_estimate,
        "age_groups": [
            {
                "code": group.code,
                "min_age": group.min_age,
                "max_age": group.max_age,
                "residents": group.residents,
                "share": group.share,
            }
            for group in item.age_groups
        ],
    }


def _totals_payload(metrics: DemographyMetricsResult) -> dict[str, Any]:
    totals = metrics.totals
    return {
        "scenario_version": metrics.scenario_version,
        "scenario_fingerprint": metrics.scenario_fingerprint,
        "employment_config_version": metrics.employment_config_version,
        "employment_config_fingerprint": metrics.employment_config_fingerprint,
        "block_count": totals.block_count,
        "area_m2": totals.area_m2,
        "population": totals.population,
        "population_density_per_km2": totals.population_density_per_km2,
        "jobs_estimate": totals.jobs_estimate,
        "age_groups": [
            {
                "code": group.code,
                "min_age": group.min_age,
                "max_age": group.max_age,
                "residents": group.residents,
                "share": group.share,
            }
            for group in totals.age_groups
        ],
    }
