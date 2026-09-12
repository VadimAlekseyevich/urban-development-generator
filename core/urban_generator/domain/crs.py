from dataclasses import dataclass

from pyproj import CRS
from pyproj.exceptions import CRSError


class CRSContractError(ValueError):
    """Raised when a CRS cannot be used as the project's metric working CRS."""


@dataclass(frozen=True, slots=True)
class WorkingCRS:
    """Validated projected CRS whose horizontal axes use metre units."""

    srid: int

    def __post_init__(self) -> None:
        _validate_metric_srid(self.srid)

    @property
    def authority(self) -> str:
        return f"EPSG:{self.srid}"

    @property
    def crs(self) -> CRS:
        return CRS.from_epsg(self.srid)


def require_working_crs(srid: int) -> WorkingCRS:
    """Validate an EPSG SRID and return the type required by metric operations."""

    return WorkingCRS(srid=srid)


def _validate_metric_srid(srid: int) -> None:
    if isinstance(srid, bool) or not isinstance(srid, int) or srid <= 0:
        raise CRSContractError("working_srid must be a positive EPSG integer")

    try:
        crs = CRS.from_epsg(srid)
    except CRSError as exc:
        raise CRSContractError(f"Unknown EPSG SRID: {srid}") from exc

    if not crs.is_projected:
        raise CRSContractError(
            f"EPSG:{srid} is not projected; metric operations require a projected working CRS"
        )

    axes = crs.axis_info[:2]
    if len(axes) < 2 or any(abs(axis.unit_conversion_factor - 1.0) > 1e-12 for axis in axes):
        raise CRSContractError(
            f"EPSG:{srid} does not use metre horizontal units and cannot be a working CRS"
        )
