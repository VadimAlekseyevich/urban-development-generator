from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import StrEnum

from shapely.geometry import Polygon, box

from core.urban_generator.buildings.config import BuildingFootprintStrategy
from core.urban_generator.buildings.envelope import (
    BuildingEnvelopeResult,
    BuildingEnvelopeStatus,
)
from core.urban_generator.buildings.placement import BuildingPlacementCandidate
from core.urban_generator.domain.crs import require_working_crs

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,255}$")


class BuildingFootprintError(ValueError):
    """Raised when S08-T04 footprint inputs violate the core contract."""


class RectangularPointFootprintKind(StrEnum):
    """Axis-aligned v1 footprint variants before S08-T07 orientation."""

    RECTANGLE = "rectangle"
    POINT = "point"


class BuildingFootprintStatus(StrEnum):
    """Deterministic result of applying one footprint spec to one anchor."""

    READY = "READY"
    OUTSIDE_ENVELOPE = "OUTSIDE_ENVELOPE"


@dataclass(frozen=True, slots=True)
class RectangularPointFootprintSpec:
    """Metric dimensions for the basic deterministic footprint strategy.

    POINT means a compact square 2D footprint, not a zero-area GIS point.
    Rotation/orientation are deliberately excluded until S08-T07.
    """

    kind: RectangularPointFootprintKind
    width_m: float
    depth_m: float

    def __post_init__(self) -> None:
        if not isinstance(self.kind, RectangularPointFootprintKind):
            raise BuildingFootprintError(
                "kind must be a RectangularPointFootprintKind value"
            )
        width = _require_positive_finite("width_m", self.width_m)
        depth = _require_positive_finite("depth_m", self.depth_m)
        if self.kind is RectangularPointFootprintKind.POINT and not math.isclose(
            width,
            depth,
            rel_tol=0.0,
            abs_tol=1e-9,
        ):
            raise BuildingFootprintError(
                "point footprint requires equal width_m and depth_m"
            )
        object.__setattr__(self, "width_m", width)
        object.__setattr__(self, "depth_m", depth)

    @classmethod
    def rectangle(
        cls,
        *,
        width_m: float,
        depth_m: float,
    ) -> RectangularPointFootprintSpec:
        return cls(
            kind=RectangularPointFootprintKind.RECTANGLE,
            width_m=width_m,
            depth_m=depth_m,
        )

    @classmethod
    def point(
        cls,
        *,
        size_m: float,
    ) -> RectangularPointFootprintSpec:
        return cls(
            kind=RectangularPointFootprintKind.POINT,
            width_m=size_m,
            depth_m=size_m,
        )

    @property
    def area_m2(self) -> float:
        return self.width_m * self.depth_m


@dataclass(frozen=True, slots=True)
class BuildingFootprintResult:
    """One proposed footprint with explicit fit status and immutable provenance."""

    source_id: str
    placement_candidate_id: str
    strategy: BuildingFootprintStrategy
    kind: RectangularPointFootprintKind
    working_srid: int
    status: BuildingFootprintStatus
    proposed_geometry: Polygon

    def __post_init__(self) -> None:
        _require_id("source_id", self.source_id)
        _require_id("placement_candidate_id", self.placement_candidate_id)
        if not isinstance(self.strategy, BuildingFootprintStrategy):
            raise BuildingFootprintError(
                "strategy must be a BuildingFootprintStrategy value"
            )
        if self.strategy is not BuildingFootprintStrategy.RECTANGULAR_POINT:
            raise BuildingFootprintError(
                "S08-T04 footprint result requires rectangular_point strategy"
            )
        if not isinstance(self.kind, RectangularPointFootprintKind):
            raise BuildingFootprintError(
                "kind must be a RectangularPointFootprintKind value"
            )
        require_working_crs(self.working_srid)
        if not isinstance(self.status, BuildingFootprintStatus):
            raise BuildingFootprintError(
                "status must be a BuildingFootprintStatus value"
            )
        if not isinstance(self.proposed_geometry, Polygon):
            raise BuildingFootprintError(
                "proposed_geometry must be a Polygon"
            )
        if (
            self.proposed_geometry.is_empty
            or not self.proposed_geometry.is_valid
            or self.proposed_geometry.has_z
        ):
            raise BuildingFootprintError(
                "proposed_geometry must be non-empty, valid and 2D"
            )
        if not math.isfinite(float(self.proposed_geometry.area)):
            raise BuildingFootprintError(
                "proposed_geometry must have finite area"
            )
        if float(self.proposed_geometry.area) <= 0.0:
            raise BuildingFootprintError(
                "proposed_geometry must have positive area"
            )

    @property
    def is_ready(self) -> bool:
        return self.status is BuildingFootprintStatus.READY

    @property
    def footprint_geometry(self) -> Polygon | None:
        return self.proposed_geometry if self.is_ready else None

    @property
    def area_m2(self) -> float:
        return float(self.proposed_geometry.area)


class RectangularPointFootprintStrategy:
    """Create an axis-aligned rectangle/square centered on a placement anchor."""

    strategy = BuildingFootprintStrategy.RECTANGULAR_POINT

    def __init__(self, *, working_srid: int) -> None:
        self.working_crs = require_working_crs(working_srid)

    def create(
        self,
        candidate: BuildingPlacementCandidate,
        *,
        envelope: BuildingEnvelopeResult,
        spec: RectangularPointFootprintSpec,
    ) -> BuildingFootprintResult:
        self._validate_inputs(
            candidate=candidate,
            envelope=envelope,
            spec=spec,
        )
        half_width = spec.width_m / 2.0
        half_depth = spec.depth_m / 2.0
        proposed = box(
            candidate.point.x - half_width,
            candidate.point.y - half_depth,
            candidate.point.x + half_width,
            candidate.point.y + half_depth,
        )
        geometry = envelope.buildable_geometry
        if geometry is None:
            raise BuildingFootprintError(
                "READY envelope must carry buildable geometry"
            )
        status = (
            BuildingFootprintStatus.READY
            if geometry.covers(proposed)
            else BuildingFootprintStatus.OUTSIDE_ENVELOPE
        )
        return BuildingFootprintResult(
            source_id=candidate.source_id,
            placement_candidate_id=candidate.candidate_id,
            strategy=self.strategy,
            kind=spec.kind,
            working_srid=self.working_crs.srid,
            status=status,
            proposed_geometry=proposed,
        )

    def _validate_inputs(
        self,
        *,
        candidate: BuildingPlacementCandidate,
        envelope: BuildingEnvelopeResult,
        spec: RectangularPointFootprintSpec,
    ) -> None:
        if not isinstance(candidate, BuildingPlacementCandidate):
            raise BuildingFootprintError(
                "candidate must be a BuildingPlacementCandidate"
            )
        if not isinstance(envelope, BuildingEnvelopeResult):
            raise BuildingFootprintError(
                "envelope must be a BuildingEnvelopeResult"
            )
        if envelope.working_srid != self.working_crs.srid:
            raise BuildingFootprintError(
                "envelope working_srid must match strategy working CRS"
            )
        if envelope.status is not BuildingEnvelopeStatus.READY:
            raise BuildingFootprintError(
                "rectangular/point footprints require a READY envelope"
            )
        if candidate.source_id != envelope.source_id:
            raise BuildingFootprintError(
                "candidate source_id must match envelope source_id"
            )
        if not isinstance(spec, RectangularPointFootprintSpec):
            raise BuildingFootprintError(
                "spec must be a RectangularPointFootprintSpec"
            )


def _require_id(field_name: str, value: str) -> None:
    if not isinstance(value, str) or _ID_RE.fullmatch(value) is None:
        raise BuildingFootprintError(
            f"invalid {field_name}: {value!r}"
        )


def _require_positive_finite(field_name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BuildingFootprintError(
            f"{field_name} must be a positive finite number"
        )
    number = float(value)
    if not math.isfinite(number) or number <= 0.0:
        raise BuildingFootprintError(
            f"{field_name} must be a positive finite number"
        )
    return number
