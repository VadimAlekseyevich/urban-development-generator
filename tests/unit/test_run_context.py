import uuid

import pytest

from core.urban_generator.domain import (
    CRSContractError,
    ConfigRef,
    CorrelationMetadata,
    DeterministicRNGFactory,
    RunContext,
    RunContextError,
    RunMode,
)


def make_context(*, seed: int, correlation_id: str = "corr-1") -> RunContext:
    return RunContext(
        run_id=uuid.uuid4(),
        mode=RunMode.EXPANSION,
        seed=seed,
        working_srid=32637,
        config_refs=(ConfigRef(name="generation", ref="synthetic:generation:v1"),),
        correlation=CorrelationMetadata(correlation_id=correlation_id),
    )


def test_run_context_exposes_typed_execution_metadata() -> None:
    context = make_context(seed=42)

    assert context.mode is RunMode.EXPANSION
    assert context.seed == 42
    assert context.working_crs.srid == 32637
    assert context.config_refs[0].name == "generation"
    assert context.correlation.correlation_id == "corr-1"


def test_same_seed_produces_same_rng_sequence_across_contexts() -> None:
    first = make_context(seed=2026, correlation_id="corr-a")
    second = make_context(seed=2026, correlation_id="corr-b")

    first_values = first.rng().integers(0, 1_000_000, size=16).tolist()
    second_values = second.rng().integers(0, 1_000_000, size=16).tolist()

    assert first.run_id != second.run_id
    assert first_values == second_values


def test_namespaced_rng_is_stable_and_order_independent() -> None:
    context = make_context(seed=99)

    roads_first = context.rng("roads").integers(0, 1_000_000, size=8).tolist()
    _ = context.rng("zoning").integers(0, 1_000_000, size=8).tolist()
    roads_second = context.rng("roads").integers(0, 1_000_000, size=8).tolist()

    assert roads_first == roads_second
    assert context.rng_seed("roads") != context.rng_seed("zoning")


def test_different_seed_produces_different_rng_stream() -> None:
    first = make_context(seed=1)
    second = make_context(seed=2)

    assert first.rng_seed("roads") != second.rng_seed("roads")


def test_rng_factory_rejects_invalid_seed_or_namespace() -> None:
    with pytest.raises(RunContextError, match="seed must be between"):
        DeterministicRNGFactory(-1)

    factory = DeterministicRNGFactory(1)
    with pytest.raises(RunContextError, match="non-empty"):
        factory.create("   ")


def test_context_requires_metric_working_crs() -> None:
    with pytest.raises(CRSContractError):
        RunContext(
            run_id=uuid.uuid4(),
            mode=RunMode.EXPANSION,
            seed=1,
            working_srid=4326,
            config_refs=(),
            correlation=CorrelationMetadata(correlation_id="corr"),
        )


def test_context_rejects_duplicate_config_names() -> None:
    with pytest.raises(RunContextError, match="duplicate config ref name"):
        RunContext(
            run_id=uuid.uuid4(),
            mode=RunMode.FROM_SCRATCH,
            seed=1,
            working_srid=32637,
            config_refs=(
                ConfigRef(name="generation", ref="config:v1"),
                ConfigRef(name="generation", ref="config:v2"),
            ),
            correlation=CorrelationMetadata(correlation_id="corr"),
        )


def test_correlation_metadata_rejects_blank_identifiers() -> None:
    with pytest.raises(RunContextError, match="correlation_id"):
        CorrelationMetadata(correlation_id=" ")
