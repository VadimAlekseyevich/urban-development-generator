import uuid
from dataclasses import dataclass

from core.urban_generator.domain.crs import WorkingCRS, require_working_crs


@dataclass(frozen=True, slots=True)
class ProjectRef:
    """Infrastructure-independent reference to a project."""

    project_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class ProjectSettings:
    """Spatial settings required by core algorithms for one project."""

    working_srid: int

    def __post_init__(self) -> None:
        require_working_crs(self.working_srid)

    @property
    def working_crs(self) -> WorkingCRS:
        return require_working_crs(self.working_srid)
