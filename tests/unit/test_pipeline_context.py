from __future__ import annotations

import uuid
from dataclasses import dataclass
from io import BytesIO
from typing import BinaryIO

import pytest

from core.urban_generator.domain import (
    ArtifactRef,
    ArtifactStat,
    ConfigRef,
    CorrelationMetadata,
    NetworkBackend,
    NetworkDistanceResult,
    NetworkGraphSnapshot,
    NetworkNodeRef,
    NetworkPath,
    NetworkPoint,
    NetworkRoutingAlgorithm,
    NetworkSnapResult,
    PipelineContext,
    PipelineContextError,
    PipelinePorts,
    ProjectRef,
    ProjectSettings,
    ResolvedConfigBinding,
    RunContext,
    RunMode,
    SnapshotLayerKind,
    SnapshotLayerRef,
    TerritorySnapshot,
    WorkingCRS,
)


@dataclass(frozen=True, slots=True)
class DummyStageConfig:
    version: str


class FakeArtifactStore:
    def put(
        self,
        ref: ArtifactRef,
        source: BinaryIO,
        *,
        content_type: str | None = None,
    ) -> ArtifactStat:
        raise NotImplementedError

    def open(self, ref: ArtifactRef) -> BinaryIO:
        return BytesIO()

    def stat(self, ref: ArtifactRef) -> ArtifactStat:
        raise NotImplementedError

    def delete(self, ref: ArtifactRef) -> None:
        return None

    def promote(self, ref: ArtifactRef) -> ArtifactStat:
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class FakeNetworkBackend:
    snapshot: NetworkGraphSnapshot

    def snap(
        self,
        point: NetworkPoint,
        *,
        max_distance_m: float,
    ) -> NetworkSnapResult | None:
        raise NotImplementedError

    def shortest_path(
        self,
        source: NetworkNodeRef,
        target: NetworkNodeRef,
        *,
        algorithm: NetworkRoutingAlgorithm = NetworkRoutingAlgorithm.DIJKSTRA,
        max_distance_m: float | None = None,
    ) -> NetworkPath | None:
        raise NotImplementedError

    def multi_source_shortest_path(
        self,
        sources: tuple[NetworkNodeRef, ...],
        target: NetworkNodeRef,
        *,
        algorithm: NetworkRoutingAlgorithm = NetworkRoutingAlgorithm.DIJKSTRA,
        max_distance_m: float | None = None,
    ) -> NetworkPath | None:
        raise NotImplementedError

    def multi_source_distances(
        self,
        sources: tuple[NetworkNodeRef, ...],
        targets: tuple[NetworkNodeRef, ...],
        *,
        max_distance_m: float | None = None,
    ) -> tuple[NetworkDistanceResult, ...]:
        raise NotImplementedError


def _run_context(*, working_srid: int = 32637) -> RunContext:
    return RunContext(
        run_id=uuid.UUID("00000000-0000-0000-0000-000000000301"),
        mode=RunMode.EXPANSION,
        seed=2026,
        working_srid=working_srid,
        config_refs=(
            ConfigRef(name="generation", ref="db:generation-run:config:v1"),
        ),
        correlation=CorrelationMetadata(correlation_id="pipeline-context-test"),
    )


def _snapshot(*, working_srid: int = 32637) -> TerritorySnapshot:
    return TerritorySnapshot(
        snapshot_id=uuid.UUID("00000000-0000-0000-0000-000000000101"),
        project=ProjectRef(
            project_id=uuid.UUID("00000000-0000-0000-0000-000000000201")
        ),
        settings=ProjectSettings(working_srid=working_srid),
        boundary=SnapshotLayerRef(
            kind=SnapshotLayerKind.BOUNDARY,
            source_ref="dataset-version:boundary:v1",
        ),
    )


def _ports(*, network_srid: int | None = None) -> PipelinePorts:
    network_backend = None
    if network_srid is not None:
        network_backend = FakeNetworkBackend(
            snapshot=NetworkGraphSnapshot(
                snapshot_id="roads:v1",
                working_crs=WorkingCRS(srid=network_srid),
                node_count=0,
                edge_count=0,
                directed=False,
            )
        )
        assert isinstance(network_backend, NetworkBackend)

    return PipelinePorts(
        artifact_store=FakeArtifactStore(),
        network_backend=network_backend,
    )


def _config_binding(
    *,
    source: ConfigRef | None = None,
    value: object | None = None,
) -> ResolvedConfigBinding:
    return ResolvedConfigBinding(
        stage_name="roads",
        source=source or ConfigRef(
            name="generation",
            ref="db:generation-run:config:v1",
        ),
        value=value or DummyStageConfig(version="1"),
    )


def test_pipeline_context_assembles_core_only_values_and_typed_config_lookup() -> None:
    context = PipelineContext(
        run=_run_context(),
        snapshot=_snapshot(),
        configs=(_config_binding(),),
        ports=_ports(network_srid=32637),
    )

    config = context.require_config("roads", DummyStageConfig)

    assert config == DummyStageConfig(version="1")
    assert context.configured_stage_names == ("roads",)
    assert context.run.working_srid == context.snapshot.settings.working_srid
    assert context.ports.network_backend is not None
    assert context.ports.network_backend.snapshot.working_crs.srid == 32637


def test_resolved_config_rejects_raw_mapping_payload() -> None:
    with pytest.raises(
        PipelineContextError,
        match="typed core object, not a mapping or None",
    ):
        _config_binding(value={"road_spacing_m": 180.0})


def test_pipeline_context_requires_config_provenance_from_run_context() -> None:
    binding = _config_binding(
        source=ConfigRef(
            name="generation",
            ref="db:generation-run:config:v2",
        )
    )

    with pytest.raises(
        PipelineContextError,
        match="source is not present in RunContext",
    ):
        PipelineContext(
            run=_run_context(),
            snapshot=_snapshot(),
            configs=(binding,),
            ports=_ports(),
        )


def test_pipeline_context_rejects_duplicate_config_keys() -> None:
    with pytest.raises(PipelineContextError, match="duplicate resolved config stage: roads"):
        PipelineContext(
            run=_run_context(),
            snapshot=_snapshot(),
            configs=(_config_binding(), _config_binding()),
            ports=_ports(),
        )


def test_pipeline_context_requires_matching_run_and_snapshot_crs() -> None:
    with pytest.raises(
        PipelineContextError,
        match="run and territory snapshot working CRS must match",
    ):
        PipelineContext(
            run=_run_context(working_srid=32637),
            snapshot=_snapshot(working_srid=3857),
            configs=(_config_binding(),),
            ports=_ports(),
        )


def test_pipeline_context_requires_matching_network_backend_crs() -> None:
    with pytest.raises(
        PipelineContextError,
        match="network backend working CRS must match",
    ):
        PipelineContext(
            run=_run_context(),
            snapshot=_snapshot(),
            configs=(_config_binding(),),
            ports=_ports(network_srid=3857),
        )


def test_pipeline_context_config_lookup_is_explicitly_typed() -> None:
    context = PipelineContext(
        run=_run_context(),
        snapshot=_snapshot(),
        configs=(_config_binding(),),
        ports=_ports(),
    )

    with pytest.raises(
        PipelineContextError,
        match="resolved config roads must be str, got DummyStageConfig",
    ):
        context.require_config("roads", str)

    with pytest.raises(
        PipelineContextError,
        match="resolved config is not available for stage: buildings",
    ):
        context.require_config("buildings", DummyStageConfig)


def test_pipeline_ports_reject_non_port_objects() -> None:
    with pytest.raises(PipelineContextError, match="ArtifactStore port"):
        PipelinePorts(artifact_store=object())  # type: ignore[arg-type]

    with pytest.raises(PipelineContextError, match="NetworkBackend port"):
        PipelinePorts(
            artifact_store=FakeArtifactStore(),
            network_backend=object(),  # type: ignore[arg-type]
        )



def test_resolved_config_binding_rejects_invalid_stage_name_and_none_value() -> None:
    source = ConfigRef(
        name="generation",
        ref="db:generation-run:config:v1",
    )

    with pytest.raises(
        PipelineContextError,
        match="invalid resolved config stage name",
    ):
        ResolvedConfigBinding(
            stage_name="Roads",
            source=source,
            value=DummyStageConfig(version="1"),
        )

    with pytest.raises(
        PipelineContextError,
        match="typed core object, not a mapping or None",
    ):
        ResolvedConfigBinding(
            stage_name="roads",
            source=source,
            value=None,
        )
