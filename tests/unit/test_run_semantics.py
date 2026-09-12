import uuid

import pytest

from core.urban_generator.domain import (
    DataOrigin,
    RunMode,
    RunSemantics,
    RunSemanticsError,
    StateOwnership,
    WorldStateContract,
)


def test_run_mode_values_are_stable() -> None:
    assert RunMode.EXPANSION.value == "EXPANSION"
    assert RunMode.FROM_SCRATCH.value == "FROM_SCRATCH"


def test_expansion_requires_existing_fixed_urban_state() -> None:
    semantics = RunSemantics(mode=RunMode.EXPANSION)

    assert semantics.requires_existing_fixed_urban_state is True
    assert semantics.allows_empty_fixed_urban_state is False
    semantics.validate_fixed_urban_state(has_fixed_urban_state=True)

    with pytest.raises(RunSemanticsError, match="EXPANSION requires"):
        semantics.validate_fixed_urban_state(has_fixed_urban_state=False)


def test_from_scratch_allows_empty_fixed_urban_state() -> None:
    semantics = RunSemantics(mode=RunMode.FROM_SCRATCH)

    assert semantics.requires_existing_fixed_urban_state is False
    assert semantics.allows_empty_fixed_urban_state is True
    semantics.validate_fixed_urban_state(has_fixed_urban_state=False)
    semantics.validate_fixed_urban_state(has_fixed_urban_state=True)


def test_fixed_source_state_is_not_run_owned() -> None:
    contract = WorldStateContract.fixed_source()

    assert contract.origin is DataOrigin.SOURCE
    assert contract.ownership is StateOwnership.FIXED
    assert contract.run_id is None
    assert contract.is_fixed_source is True
    assert contract.is_generated is False


def test_generated_state_is_owned_by_a_specific_run() -> None:
    run_id = uuid.uuid4()

    contract = WorldStateContract.generated_for(run_id)

    assert contract.origin is DataOrigin.GENERATED
    assert contract.ownership is StateOwnership.GENERATED
    assert contract.run_id == run_id
    assert contract.is_generated is True
    assert contract.is_fixed_source is False


def test_fixed_state_cannot_be_generated() -> None:
    with pytest.raises(RunSemanticsError, match="fixed world state"):
        WorldStateContract(
            origin=DataOrigin.GENERATED,
            ownership=StateOwnership.FIXED,
        )


def test_fixed_source_state_cannot_belong_to_a_run() -> None:
    with pytest.raises(RunSemanticsError, match="cannot be owned"):
        WorldStateContract(
            origin=DataOrigin.SOURCE,
            ownership=StateOwnership.FIXED,
            run_id=uuid.uuid4(),
        )


def test_generated_state_requires_generated_origin_and_run_id() -> None:
    with pytest.raises(RunSemanticsError, match="GENERATED origin"):
        WorldStateContract(
            origin=DataOrigin.SOURCE,
            ownership=StateOwnership.GENERATED,
            run_id=uuid.uuid4(),
        )

    with pytest.raises(RunSemanticsError, match="run UUID"):
        WorldStateContract(
            origin=DataOrigin.GENERATED,
            ownership=StateOwnership.GENERATED,
        )
