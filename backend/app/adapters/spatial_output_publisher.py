"""Existing writer composition for actual spatial stage outputs of an unfinished run."""

from __future__ import annotations

import uuid
from collections.abc import Callable

from sqlalchemy.orm import Session

from backend.app.application.generation import GenerationExecutionError, StageCompletionInput
from backend.app.db.generated_block_parcel_writer import SqlAlchemyGeneratedBlockParcelWriter
from backend.app.db.generated_building_writer import SqlAlchemyGeneratedBuildingWriter
from backend.app.db.generated_demography_writer import SqlAlchemyGeneratedDemographyWriter
from backend.app.db.generated_infrastructure_writer import SqlAlchemyGeneratedInfrastructureWriter
from backend.app.db.generated_road_writer import SqlAlchemyGeneratedRoadWriter
from backend.app.db.generated_zone_writer import SqlAlchemyGeneratedZoneWriter
from backend.app.db.session import SessionLocal
from backend.app.db.stage_artifact_publication import SqlAlchemyStageArtifactPublisher
from core.urban_generator.domain import ArtifactRef, ArtifactStore, StageResult
from core.urban_generator.stages import (
    BlocksAndParcelsStage,
    BlocksAndParcelsStageOutput,
    BuildingStage,
    BuildingStageOutput,
    ConstraintMaskStage,
    ConstraintMaskStageInput,
    DemographyStage,
    DemographyStageOutput,
    InfrastructureStage,
    InfrastructureStageOutput,
    PrepareSnapshotStage,
    RoadStage,
    RoadStageOutput,
    SuitabilityStage,
    SuitabilityStageConfig,
    SuitabilityStageInput,
    ZoningStage,
    ZoningStageOutput,
)
from core.urban_generator.suitability import (
    HardExclusionMask,
    SuitabilityArtifactWriter,
    WeightedSuitabilityResult,
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
        self._infrastructure = SqlAlchemyGeneratedInfrastructureWriter(
            session_factory=session_factory,
        )

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
        elif stage_name == InfrastructureStage.name and isinstance(
            output, InfrastructureStageOutput,
        ):
            for item in output.types:
                self._infrastructure.replace(
                    run_id=run_id, state=item.placement, candidate_geometry=item.candidate_geometry,
                    candidate_snaps=item.snaps, infrastructure_type=item.infrastructure_type,
                )
        else:
            raise GenerationExecutionError(f"unsupported spatial output: {stage_name}")


class TransactionalSpatialMaterializer:
    """Commit GIS rows and artifact ownership with the authoritative stage row.

    Existing writers use savepoints on the already locked DB connection, not
    independent transactions. The temporary/ready blob is external to SQL;
    existing orphan GC is responsible for unreferenced bytes after rollback.
    """

    def __init__(self, *, artifact_store: ArtifactStore) -> None:
        self._store = artifact_store

    def materialize(
        self, *, session: Session, run_id: uuid.UUID, stage_name: str,
        result: StageResult[object], completion: StageCompletionInput,
    ) -> None:
        if not session.in_transaction():
            raise GenerationExecutionError("materialization requires an active transaction")
        connection = session.connection()

        def nested_session() -> Session:
            return Session(
                bind=connection, join_transaction_mode="create_savepoint",
                expire_on_commit=False,
            )

        output = result.output
        if stage_name == PrepareSnapshotStage.name:
            if not isinstance(output, ConstraintMaskStageInput):
                raise GenerationExecutionError("invalid prepared territory output")
            return
        if stage_name == ConstraintMaskStage.name:
            if not isinstance(output, HardExclusionMask):
                raise GenerationExecutionError("invalid hard mask output")
            return
        if stage_name == SuitabilityStage.name:
            if (
                not isinstance(output, WeightedSuitabilityResult)
                or not isinstance(completion.stage_input, SuitabilityStageInput)
                or not isinstance(completion.config, SuitabilityStageConfig)
            ):
                raise GenerationExecutionError("invalid suitability publication inputs")
            # A stage row forbids automatic replay after side effects. A unique
            # key avoids replacing earlier bytes if a publication fails.
            ref = ArtifactRef(f"runs/{run_id}/stages/suitability/{uuid.uuid4()}.tif")
            artifact = SuitabilityArtifactWriter().write(
                self._store, output_ref=ref, result=output,
                config=completion.config.suitability,
                hard_mask=completion.stage_input.hard_mask,
                promote=False,
            )
            SqlAlchemyStageArtifactPublisher(
                store=self._store, session_factory=nested_session,
            ).publish(run_id=run_id, stage_name=stage_name, temporary_stat=artifact.stat)
            return
        SpatialOutputPublisher(session_factory=nested_session).publish(
            run_id=run_id, stage_name=stage_name, output=output,
        )
