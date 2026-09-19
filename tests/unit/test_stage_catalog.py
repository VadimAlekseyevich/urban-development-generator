from core.urban_generator.constraints import ConstraintRegistry, RegisteredConstraintEngine
from core.urban_generator.domain import Stage, validate_stage_metadata
from core.urban_generator.stages import (
    CANONICAL_STAGE_DEPENDENCIES,
    CANONICAL_STAGE_ORDER,
    BlocksAndParcelsStage,
    BuildingStage,
    ConstraintMaskStage,
    DemographyStage,
    RoadStage,
    SuitabilityStage,
    ZoningStage,
)


def test_canonical_stage_catalog_is_unique_and_dependency_ordered() -> None:
    assert len(CANONICAL_STAGE_ORDER) == len(set(CANONICAL_STAGE_ORDER))
    positions = {stage: index for index, stage in enumerate(CANONICAL_STAGE_ORDER)}

    assert set(CANONICAL_STAGE_DEPENDENCIES) == set(CANONICAL_STAGE_ORDER)
    for stage in CANONICAL_STAGE_ORDER:
        dependencies = CANONICAL_STAGE_DEPENDENCIES[stage]
        validate_stage_metadata(stage, "1.0.0", dependencies)
        for dependency in dependencies:
            assert positions[dependency] < positions[stage]



def test_all_implemented_stage_adapters_satisfy_canonical_protocol() -> None:
    stages = (
        ConstraintMaskStage(),
        SuitabilityStage(factors=()),
        ZoningStage(
            constraint_engine=RegisteredConstraintEngine(ConstraintRegistry())
        ),
        RoadStage(),
        BlocksAndParcelsStage(),
        BuildingStage(),
        DemographyStage(),
    )

    for stage in stages:
        assert isinstance(stage, Stage)
        validate_stage_metadata(stage.name, stage.version, stage.dependencies)
        assert stage.dependencies == CANONICAL_STAGE_DEPENDENCIES[stage.name]
