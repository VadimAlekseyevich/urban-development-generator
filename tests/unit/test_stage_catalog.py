from core.urban_generator.domain import validate_stage_metadata
from core.urban_generator.stages import (
    CANONICAL_STAGE_DEPENDENCIES,
    CANONICAL_STAGE_ORDER,
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
