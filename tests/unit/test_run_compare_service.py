"""Pure ordering and request-boundary regression for the S12 read-only compare."""

import uuid

import pytest

from backend.app.application.run_compare import (
    RunCompareQueryError,
    RunCompareService,
    _ranks,
)
from core.urban_generator.domain.benchmarking import MetricDirection


class FakeRepository:
    def __init__(self) -> None:
        self.reads = 0

    def get_runs(self, *, project_id, run_ids):
        self.reads += 1
        return []

    def project_exists(self, *, project_id):
        return False


@pytest.mark.parametrize(
    ("direction", "expected"),
    [
        (MetricDirection.LOWER_IS_BETTER, (1, 3, 1, None)),
        (MetricDirection.HIGHER_IS_BETTER, (2, 1, 2, None)),
        (MetricDirection.TARGET, (None, None, None, None)),
        (MetricDirection.DESCRIPTIVE, (None, None, None, None)),
    ],
)
def test_ranks_preserve_ties_missing_and_direction(
    direction: MetricDirection, expected: tuple[int | None, ...]
) -> None:
    assert _ranks((1.0, 4.0, 1.0, None), direction=direction) == expected


def test_duplicate_and_oversized_run_ids_fail_before_database_read() -> None:
    repository = FakeRepository()
    service = RunCompareService(repository)
    project_id = uuid.uuid4()
    run_id = uuid.uuid4()
    for ids in (
        (run_id,),
        (run_id, run_id),
        tuple(uuid.uuid4() for _ in range(11)),
        ("not-a-uuid", uuid.uuid4()),
    ):
        with pytest.raises(RunCompareQueryError, match="unique UUIDs"):
            service.compare(project_id=project_id, run_ids=ids)  # type: ignore[arg-type]
    assert repository.reads == 0
