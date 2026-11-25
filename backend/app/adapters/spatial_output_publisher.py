"""Existing writer composition for actual spatial stage outputs of an unfinished run."""

from __future__ import annotations

import uuid
from collections.abc import Callable

from sqlalchemy.orm import Session

from backend.app.application.generation import GenerationExecutionError
from backend.app.db.generated_block_parcel_writer import SqlAlchemyGeneratedBlockParcelWriter
from backend.app.db.generated_building_writer import SqlAlchemyGeneratedBuildingWriter
from backend.app.db.generated_demography_writer import SqlAlchemyGeneratedDemographyWriter
from backend.app.db.generated_road_writer import SqlAlchemyGeneratedRoadWriter
from backend.app.db.generated_zone_writer import SqlAlchemyGeneratedZoneWriter
from backend.app.db.session import SessionLocal
from core.urban_generator.stages import (
    BlocksAndParcelsStage,
    BlocksAndParcelsStageOutput,
    BuildingStage,
    BuildingStageOutput,
    DemographyStage,
    DemographyStageOutput,
    RoadStage,
    RoadStageOutput,
    ZoningStage,
    ZoningStageOutput,
)


class SpatialOutputPublisher:
    """Publish typed outputs in dependency order using existing run-scoped writers.

    Each writer owns its transaction. This does not complete a stage or run,
    and must not be mistaken for final job publication.
    """

    def __init__(
        self, *, session_factory: Callable[[], Session] = SessionLocal,
    ) -> None:
        self._zones = SqlAlchemyGeneratedZoneWriter(session_factory=session_factory)
        self._roads = SqlAlchemyGeneratedRoadWriter(session_factory=session_factory)
        self._blocks = SqlAlchemyGeneratedBlockParcelWriter(session_factory=session_factory)
        self._buildings = SqlAlchemyGeneratedBuildingWriter(session_factory=session_factory)
        self._demography = SqlAlchemyGeneratedDemographyWriter(session_factory=session_factory)

    def publish(self, *, run_id: uuid.UUID, stage_name: str, output: object) -> None:
        if stage_name == ZoningStage.name and isinstance(output, ZoningStageOutput):
            self._zones.replace(
                run_id=run_id, partition=output.partition,
                assignment=output.assignment, constraint_evaluation=output.constraints,
            )
        elif stage_name == RoadStage.name and isinstance(output, RoadStageOutput):
            self._roads.replace(
                run_id=run_id, graph=output.graph, classification=output.classification,
            )
        elif stage_name == BlocksAndParcelsStage.name and isinstance(
            output, BlocksAndParcelsStageOutput,
        ):
            self._blocks.replace(
                run_id=run_id, zoned_blocks=output.association, subdivision=output.subdivision,
            )
        elif stage_name == BuildingStage.name and isinstance(output, BuildingStageOutput):
            self._buildings.replace(
                run_id=run_id, subjects=output.area_subjects, assignment=output.attributes,
                area_result=output.area_metrics,
            )
        elif stage_name == DemographyStage.name and isinstance(output, DemographyStageOutput):
            self._demography.replace(run_id=run_id, aggregation=output.aggregation)
        else:
            raise GenerationExecutionError(f"unsupported spatial output: {stage_name}")
