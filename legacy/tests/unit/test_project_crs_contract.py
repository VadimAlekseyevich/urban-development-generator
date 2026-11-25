import uuid

import pytest

from core.urban_generator.domain import (
    CRSContractError,
    ProjectRef,
    ProjectSettings,
    WorkingCRS,
    require_working_crs,
)


def test_project_ref_is_infrastructure_independent_value_object() -> None:
    project_id = uuid.uuid4()

    ref = ProjectRef(project_id=project_id)

    assert ref.project_id == project_id


def test_project_settings_require_metric_working_crs() -> None:
    settings = ProjectSettings(working_srid=32637)

    assert settings.working_srid == 32637
    assert settings.working_crs == WorkingCRS(32637)
    assert settings.working_crs.authority == "EPSG:32637"


def test_geographic_crs_is_rejected_for_metric_operations() -> None:
    with pytest.raises(CRSContractError, match="not projected"):
        ProjectSettings(working_srid=4326)


def test_non_metre_projected_crs_is_rejected() -> None:
    with pytest.raises(CRSContractError, match="metre"):
        require_working_crs(2263)


def test_invalid_srid_is_rejected() -> None:
    with pytest.raises(CRSContractError, match="positive EPSG integer"):
        require_working_crs(0)


def test_metric_operation_contract_accepts_only_validated_working_crs() -> None:
    working_crs = require_working_crs(32637)

    assert isinstance(working_crs, WorkingCRS)
    assert working_crs.srid == 32637
