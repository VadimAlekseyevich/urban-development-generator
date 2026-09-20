from __future__ import annotations

import math
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.app.db.session import SessionLocal
from backend.app.models.generated_entity import GeneratedBlock, GeneratedInfrastructure
from backend.app.models.generation_run import RUN_SUCCESS_STATUS, GenerationRun
from core.urban_generator.domain import DataError
from core.urban_generator.infrastructure import (
    ExistingInfrastructureFacilityRef,
    ExistingInfrastructureResult,
    InfrastructureAccessibilityBatchResult,
    InfrastructureAccessibilityMode,
    InfrastructureGreedyPlacementState,
    InfrastructureMetricsBuilder,
    InfrastructureMetricsResult,
    InfrastructureType,
    UnmetDemandResult,
)

READ_MODEL_VERSION = "infrastructure-ui-v1"
DEFAULT_MAX_INFRASTRUCTURE_UI_BLOCKS = 100_000
DEFAULT_MAX_INFRASTRUCTURE_UI_FACILITIES = 100_000


class InfrastructureUiReadModelPersistenceError(DataError):
    """Base error for persisted S10 infrastructure presentation read-models."""


class InfrastructureUiReadModelImmutableError(
    InfrastructureUiReadModelPersistenceError
):
    """Raised before mutating infrastructure presentation data of a successful run."""


@dataclass(frozen=True, slots=True)
class InfrastructureUiReadModelWriteResult:
    run_id: uuid.UUID
    updated_block_rows: int
    updated_generated_facility_rows: int
    existing_facility_summary_count: int
    generated_facility_summary_count: int
    metrics: InfrastructureMetricsResult


class SqlAlchemyInfrastructureUiReadModelWriter:
    """Persist authoritative T03/T07/T08/T11 projections for S10 map reads."""

    def __init__(
        self,
        *,
        session_factory: Callable[[], Session] = SessionLocal,
        max_blocks: int = DEFAULT_MAX_INFRASTRUCTURE_UI_BLOCKS,
        max_facilities: int = DEFAULT_MAX_INFRASTRUCTURE_UI_FACILITIES,
    ) -> None:
        _require_positive_int("max_blocks", max_blocks)
        _require_positive_int("max_facilities", max_facilities)
        if max_blocks > DEFAULT_MAX_INFRASTRUCTURE_UI_BLOCKS:
            raise ValueError(
                "max_blocks exceeds infrastructure UI read-model hard limit"
            )
        if max_facilities > DEFAULT_MAX_INFRASTRUCTURE_UI_FACILITIES:
            raise ValueError(
                "max_facilities exceeds infrastructure UI read-model hard limit"
            )
        self._session_factory = session_factory
        self._max_blocks = max_blocks
        self._max_facilities = max_facilities
        self._metrics_builder = InfrastructureMetricsBuilder()

    def replace(
        self,
        *,
        run_id: uuid.UUID,
        unmet_demand: UnmetDemandResult,
        placements: tuple[InfrastructureGreedyPlacementState, ...],
        existing_accessibility: tuple[InfrastructureAccessibilityBatchResult, ...],
        existing: ExistingInfrastructureResult,
        infrastructure_types: tuple[InfrastructureType, ...],
    ) -> InfrastructureUiReadModelWriteResult:
        if not isinstance(run_id, uuid.UUID):
            raise TypeError("run_id must be UUID")
        if not isinstance(unmet_demand, UnmetDemandResult):
            raise TypeError("unmet_demand must be UnmetDemandResult")
        if not isinstance(placements, tuple):
            raise TypeError("placements must be an immutable tuple")
        if not isinstance(existing_accessibility, tuple):
            raise TypeError("existing_accessibility must be an immutable tuple")
        if not isinstance(existing, ExistingInfrastructureResult):
            raise TypeError("existing must be ExistingInfrastructureResult")
        if not isinstance(infrastructure_types, tuple):
            raise TypeError("infrastructure_types must be an immutable tuple")

        metrics = self._metrics_builder.build(
            unmet_demand,
            infrastructure_types=infrastructure_types,
            placements=placements,
            existing_accessibility=existing_accessibility,
        )
        type_by_code = _type_by_code(infrastructure_types)
        placement_by_code = {
            item.infrastructure_type_code: item for item in placements
        }
        block_payloads = _block_payloads(
            unmet_demand=unmet_demand,
            placement_by_code=placement_by_code,
        )
        if len(block_payloads) > self._max_blocks:
            raise InfrastructureUiReadModelPersistenceError(
                "infrastructure UI block limit exceeded"
            )

        existing_summaries = _existing_accessibility_summaries(
            existing=existing,
            batches=existing_accessibility,
            type_by_code=type_by_code,
        )
        generated_summaries = _generated_accessibility_summaries(
            placements=placements,
            type_by_code=type_by_code,
        )
        if len(existing_summaries) + len(generated_summaries) > self._max_facilities:
            raise InfrastructureUiReadModelPersistenceError(
                "infrastructure UI facility summary limit exceeded"
            )

        try:
            with self._session_factory() as session:
                with session.begin():
                    run = session.scalars(
                        select(GenerationRun)
                        .where(GenerationRun.id == run_id)
                        .with_for_update()
                    ).one_or_none()
                    if run is None:
                        raise InfrastructureUiReadModelPersistenceError(
                            f"generation run not found: {run_id}"
                        )
                    if run.status == RUN_SUCCESS_STATUS:
                        raise InfrastructureUiReadModelImmutableError(
                            "infrastructure read-model of a successful generation "
                            "run is immutable"
                        )

                    blocks = list(
                        session.scalars(
                            select(GeneratedBlock)
                            .where(GeneratedBlock.run_id == run_id)
                            .order_by(GeneratedBlock.block_key, GeneratedBlock.id)
                            .with_for_update()
                        ).all()
                    )
                    block_by_key = _validate_blocks(
                        blocks=blocks,
                        payloads=block_payloads,
                    )

                    generated_rows = list(
                        session.scalars(
                            select(GeneratedInfrastructure)
                            .where(GeneratedInfrastructure.run_id == run_id)
                            .order_by(
                                GeneratedInfrastructure.infrastructure_type_code,
                                GeneratedInfrastructure.acceptance_index,
                                GeneratedInfrastructure.id,
                            )
                            .with_for_update()
                        ).all()
                    )
                    generated_by_key = _validate_generated_facilities(
                        rows=generated_rows,
                        summaries=generated_summaries,
                    )

                    for block_key, payload in block_payloads.items():
                        block = block_by_key[block_key]
                        attributes = dict(block.attributes_json or {})
                        attributes["infrastructure"] = payload
                        block.attributes_json = attributes

                    for key, summary in generated_summaries.items():
                        row = generated_by_key[key]
                        attributes = dict(row.attributes_json or {})
                        attributes["accessibility"] = summary
                        attributes["max_network_distance_m"] = summary[
                            "max_network_distance_m"
                        ]
                        row.attributes_json = attributes

                    run_metrics = dict(run.metrics_json or {})
                    run_metrics["infrastructure"] = _run_payload(
                        unmet_demand=unmet_demand,
                        metrics=metrics,
                        existing_summaries=existing_summaries,
                        generated_summaries=generated_summaries,
                    )
                    run.metrics_json = run_metrics

            return InfrastructureUiReadModelWriteResult(
                run_id=run_id,
                updated_block_rows=len(block_payloads),
                updated_generated_facility_rows=len(generated_summaries),
                existing_facility_summary_count=len(existing_summaries),
                generated_facility_summary_count=len(generated_summaries),
                metrics=metrics,
            )
        except InfrastructureUiReadModelPersistenceError:
            raise
        except SQLAlchemyError as exc:
            raise InfrastructureUiReadModelPersistenceError(
                f"unable to persist infrastructure UI read-model for run {run_id}"
            ) from exc


def _type_by_code(
    infrastructure_types: tuple[InfrastructureType, ...],
) -> dict[str, InfrastructureType]:
    result: dict[str, InfrastructureType] = {}
    for item in infrastructure_types:
        if not isinstance(item, InfrastructureType):
            raise InfrastructureUiReadModelPersistenceError(
                "infrastructure_types must contain InfrastructureType values"
            )
        if item.code in result:
            raise InfrastructureUiReadModelPersistenceError(
                f"duplicate infrastructure type code: {item.code}"
            )
        result[item.code] = item
    return result


def _block_payloads(
    *,
    unmet_demand: UnmetDemandResult,
    placement_by_code: dict[str, InfrastructureGreedyPlacementState],
) -> dict[str, dict[str, Any]]:
    final_by_key: dict[tuple[str, str], float] = {}
    for type_code, placement in placement_by_code.items():
        for state in placement.remaining_demand:
            if state.demand_ref.infrastructure_type_code != type_code:
                raise InfrastructureUiReadModelPersistenceError(
                    "placement demand type does not match placement state"
                )
            final_by_key[state.key] = state.remaining_demand

    grouped: dict[str, list[dict[str, Any]]] = {}
    for demand in unmet_demand.demands:
        final_remaining = final_by_key.get(demand.key)
        if final_remaining is None:
            raise InfrastructureUiReadModelPersistenceError(
                "placement state is missing final demand for "
                f"{demand.block_id}/{demand.infrastructure_type_code}"
            )
        if final_remaining > demand.unmet_demand and not math.isclose(
            final_remaining,
            demand.unmet_demand,
            rel_tol=1e-12,
            abs_tol=1e-9,
        ):
            raise InfrastructureUiReadModelPersistenceError(
                "final remaining demand cannot exceed T03 unmet demand"
            )
        grouped.setdefault(demand.block_id, []).append(
            {
                "infrastructure_type_code": demand.infrastructure_type_code,
                "category": demand.infrastructure_category.value,
                "demographic_signal": demand.demographic_signal.value,
                "demographic_group": demand.demographic_group,
                "gross_demand": demand.gross_demand,
                "existing_served_demand": demand.served_demand,
                "initial_unmet_demand": demand.unmet_demand,
                "generated_served_demand": max(
                    demand.unmet_demand - final_remaining,
                    0.0,
                ),
                "final_unmet_demand": final_remaining,
            }
        )

    payloads: dict[str, dict[str, Any]] = {}
    for block_id in sorted(grouped):
        rows = sorted(
            grouped[block_id],
            key=lambda item: str(item["infrastructure_type_code"]),
        )
        gross = math.fsum(float(item["gross_demand"]) for item in rows)
        final_unmet = math.fsum(
            float(item["final_unmet_demand"]) for item in rows
        )
        total_served = max(gross - final_unmet, 0.0)
        payloads[block_id] = {
            "read_model_version": READ_MODEL_VERSION,
            "scenario_version": unmet_demand.scenario_version,
            "scenario_fingerprint": unmet_demand.scenario_fingerprint,
            "gross_demand": gross,
            "served_demand": total_served,
            "final_unmet_demand": final_unmet,
            "coverage_ratio": total_served / gross if gross > 0.0 else 0.0,
            "demands": rows,
        }
    return payloads


def _existing_accessibility_summaries(
    *,
    existing: ExistingInfrastructureResult,
    batches: tuple[InfrastructureAccessibilityBatchResult, ...],
    type_by_code: dict[str, InfrastructureType],
) -> dict[str, dict[str, Any]]:
    facilities = {item.facility_id: item for item in existing.facilities}
    summaries: dict[str, dict[str, Any]] = {}

    for facility in existing.facilities:
        infrastructure_type = type_by_code.get(facility.infrastructure_type_code)
        if infrastructure_type is None:
            raise InfrastructureUiReadModelPersistenceError(
                "existing facility references unknown infrastructure type: "
                f"{facility.infrastructure_type_code}"
            )
        summaries[facility.facility_id] = {
            "origin": "existing",
            "facility_id": facility.facility_id,
            "source_ref": facility.source_ref,
            "source_feature_id": facility.source_feature_id,
            "infrastructure_type_code": facility.infrastructure_type_code,
            "capacity": facility.capacity,
            "max_network_distance_m": infrastructure_type.max_network_distance_m,
            "reachable_demand_count": 0,
            "nearest_distance_m": None,
            "farthest_distance_m": None,
        }

    distances_by_facility: dict[str, list[float]] = {
        key: [] for key in summaries
    }
    for batch in batches:
        if batch.mode is not InfrastructureAccessibilityMode.EXISTING_FACILITY:
            raise InfrastructureUiReadModelPersistenceError(
                "existing accessibility batch must use existing_facility mode"
            )
        for item in batch.reachable:
            ref = item.facility_site_ref
            if not isinstance(ref, ExistingInfrastructureFacilityRef):
                raise InfrastructureUiReadModelPersistenceError(
                    "existing accessibility result must reference existing facility"
                )
            facility = facilities.get(ref.facility_id)
            if facility is None:
                raise InfrastructureUiReadModelPersistenceError(
                    "existing accessibility references unknown facility: "
                    f"{ref.facility_id}"
                )
            if facility.infrastructure_type_code != batch.infrastructure_type_code:
                raise InfrastructureUiReadModelPersistenceError(
                    "existing accessibility facility type mismatch"
                )
            distances_by_facility[ref.facility_id].append(item.distance_m)

    for facility_id, distances in distances_by_facility.items():
        summary = summaries[facility_id]
        summary["reachable_demand_count"] = len(distances)
        if distances:
            summary["nearest_distance_m"] = min(distances)
            summary["farthest_distance_m"] = max(distances)
    return summaries


def _generated_accessibility_summaries(
    *,
    placements: tuple[InfrastructureGreedyPlacementState, ...],
    type_by_code: dict[str, InfrastructureType],
) -> dict[tuple[str, str], dict[str, Any]]:
    summaries: dict[tuple[str, str], dict[str, Any]] = {}
    for placement in placements:
        infrastructure_type = type_by_code.get(
            placement.infrastructure_type_code
        )
        if infrastructure_type is None:
            raise InfrastructureUiReadModelPersistenceError(
                "placement references unknown infrastructure type: "
                f"{placement.infrastructure_type_code}"
            )
        cache_by_key = {
            item.candidate_ref.key: item for item in placement.coverage_cache
        }
        for accepted in placement.accepted_facilities:
            key = accepted.candidate_ref.key
            cache = cache_by_key.get(key)
            if cache is None:
                raise InfrastructureUiReadModelPersistenceError(
                    f"accepted candidate coverage cache is missing: {key!r}"
                )
            distances = [item.distance_m for item in cache.accessibility]
            summaries[key] = {
                "origin": "generated",
                "candidate_id": accepted.candidate_ref.candidate_id,
                "infrastructure_type_code": placement.infrastructure_type_code,
                "acceptance_index": accepted.acceptance_index,
                "network_snapshot_id": placement.snapshot_id,
                "max_network_distance_m": infrastructure_type.max_network_distance_m,
                "reachable_demand_count": len(distances),
                "nearest_distance_m": min(distances) if distances else None,
                "farthest_distance_m": max(distances) if distances else None,
            }
    return summaries


def _validate_blocks(
    *,
    blocks: list[GeneratedBlock],
    payloads: dict[str, dict[str, Any]],
) -> dict[str, GeneratedBlock]:
    by_key: dict[str, GeneratedBlock] = {}
    for block in blocks:
        if block.block_key is None or not block.block_key:
            raise InfrastructureUiReadModelPersistenceError(
                "generated block must have block_key before infrastructure read-model persistence"
            )
        if block.block_key in by_key:
            raise InfrastructureUiReadModelPersistenceError(
                f"duplicate generated block_key: {block.block_key}"
            )
        by_key[block.block_key] = block
    if set(by_key) != set(payloads):
        raise InfrastructureUiReadModelPersistenceError(
            "generated blocks and infrastructure demand must contain identical block ids"
        )
    return by_key


def _validate_generated_facilities(
    *,
    rows: list[GeneratedInfrastructure],
    summaries: dict[tuple[str, str], dict[str, Any]],
) -> dict[tuple[str, str], GeneratedInfrastructure]:
    by_key: dict[tuple[str, str], GeneratedInfrastructure] = {}
    for row in rows:
        if row.candidate_id is None or row.infrastructure_type_code is None:
            raise InfrastructureUiReadModelPersistenceError(
                "generated infrastructure must use the typed persistence shape"
            )
        key = (row.candidate_id, row.infrastructure_type_code)
        if key in by_key:
            raise InfrastructureUiReadModelPersistenceError(
                f"duplicate generated infrastructure candidate/type: {key!r}"
            )
        by_key[key] = row
    if set(by_key) != set(summaries):
        raise InfrastructureUiReadModelPersistenceError(
            "persisted generated infrastructure must match accepted facilities exactly"
        )
    return by_key


def _run_payload(
    *,
    unmet_demand: UnmetDemandResult,
    metrics: InfrastructureMetricsResult,
    existing_summaries: dict[str, dict[str, Any]],
    generated_summaries: dict[tuple[str, str], dict[str, Any]],
) -> dict[str, Any]:
    return {
        "read_model_version": READ_MODEL_VERSION,
        "scenario_version": unmet_demand.scenario_version,
        "scenario_fingerprint": unmet_demand.scenario_fingerprint,
        "raw_metrics": [_metric_payload(item) for item in metrics.raw_metrics],
        "diagnostics": {
            "infrastructure_type_count": metrics.diagnostics.infrastructure_type_count,
            "demand_item_count": metrics.diagnostics.demand_item_count,
            "accepted_generated_facility_count": (
                metrics.diagnostics.accepted_generated_facility_count
            ),
            "existing_distance_sample_count": (
                metrics.diagnostics.existing_distance_sample_count
            ),
            "generated_distance_sample_count": (
                metrics.diagnostics.generated_distance_sample_count
            ),
        },
        "facility_accessibility": {
            "existing": [
                existing_summaries[key] for key in sorted(existing_summaries)
            ],
            "generated": [
                generated_summaries[key] for key in sorted(generated_summaries)
            ],
        },
    }


def _metric_payload(item: Any) -> dict[str, Any]:
    if item.scalar_value is not None:
        return {
            "metric_id": item.metric_id.value,
            "scalar_value": item.scalar_value,
        }
    if item.age_coverage:
        return {
            "metric_id": item.metric_id.value,
            "age_coverage": [
                {
                    "demographic_group": value.demographic_group,
                    "population": value.population,
                    "covered_population": value.covered_population,
                    "coverage_ratio": value.coverage_ratio,
                }
                for value in item.age_coverage
            ],
        }
    return {
        "metric_id": item.metric_id.value,
        "scalar_value": None,
    }


def _require_positive_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field_name} must be a positive integer")
