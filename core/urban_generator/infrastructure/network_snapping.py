from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from shapely.geometry import Point

from core.urban_generator.blocks import BlockZoneAssociationResult
from core.urban_generator.domain import (
    NetworkBackend,
    NetworkNodeRef,
    NetworkPoint,
    require_max_distance_m,
)
from core.urban_generator.infrastructure.demand import UnmetDemandResult
from core.urban_generator.infrastructure.existing import ExistingInfrastructureResult
from core.urban_generator.infrastructure.site_geometry import (
    InfrastructureCandidateGeometryResult,
)

DEFAULT_MAX_INFRASTRUCTURE_SNAP_SUBJECTS = 500_000


class InfrastructureNetworkSnapError(ValueError):
    """Raised when S10-T06 network snapping violates its bounded contract."""


class InfrastructureNetworkSiteKind(StrEnum):
    CANDIDATE = "candidate"
    EXISTING = "existing"


@dataclass(frozen=True, slots=True)
class InfrastructureDemandNetworkSnap:
    """Nearest road-network node for one unique demand block."""

    block_id: str
    point: NetworkPoint
    node: NetworkNodeRef
    snap_distance_m: float

    def __post_init__(self) -> None:
        _require_id("block_id", self.block_id)
        if not isinstance(self.point, NetworkPoint):
            raise InfrastructureNetworkSnapError(
                "point must be a NetworkPoint"
            )
        if not isinstance(self.node, NetworkNodeRef):
            raise InfrastructureNetworkSnapError(
                "node must be a NetworkNodeRef"
            )
        distance = _require_non_negative_finite(
            "snap_distance_m",
            self.snap_distance_m,
        )
        object.__setattr__(self, "snap_distance_m", distance)


@dataclass(frozen=True, slots=True)
class InfrastructureSiteNetworkSnap:
    """Nearest road-network node for a generated or existing facility site."""

    site_kind: InfrastructureNetworkSiteKind
    site_id: str
    infrastructure_type_code: str
    point: NetworkPoint
    node: NetworkNodeRef
    snap_distance_m: float

    def __post_init__(self) -> None:
        if not isinstance(self.site_kind, InfrastructureNetworkSiteKind):
            raise InfrastructureNetworkSnapError(
                "site_kind must be InfrastructureNetworkSiteKind"
            )
        _require_id("site_id", self.site_id)
        _require_id(
            "infrastructure_type_code",
            self.infrastructure_type_code,
        )
        if not isinstance(self.point, NetworkPoint):
            raise InfrastructureNetworkSnapError(
                "point must be a NetworkPoint"
            )
        if not isinstance(self.node, NetworkNodeRef):
            raise InfrastructureNetworkSnapError(
                "node must be a NetworkNodeRef"
            )
        distance = _require_non_negative_finite(
            "snap_distance_m",
            self.snap_distance_m,
        )
        object.__setattr__(self, "snap_distance_m", distance)

    @property
    def key(self) -> tuple[str, str]:
        return self.site_kind.value, self.site_id


@dataclass(frozen=True, slots=True)
class InfrastructureUnsnappedSite:
    site_kind: InfrastructureNetworkSiteKind
    site_id: str
    infrastructure_type_code: str

    def __post_init__(self) -> None:
        if not isinstance(self.site_kind, InfrastructureNetworkSiteKind):
            raise InfrastructureNetworkSnapError(
                "site_kind must be InfrastructureNetworkSiteKind"
            )
        _require_id("site_id", self.site_id)
        _require_id(
            "infrastructure_type_code",
            self.infrastructure_type_code,
        )

    @property
    def key(self) -> tuple[str, str]:
        return self.site_kind.value, self.site_id


@dataclass(frozen=True, slots=True)
class InfrastructureNetworkSnapDiagnostics:
    demand_subject_count: int
    candidate_site_count: int
    existing_site_count: int
    snapped_demand_count: int
    snapped_site_count: int
    unsnapped_demand_count: int
    unsnapped_site_count: int

    def __post_init__(self) -> None:
        for field_name in (
            "demand_subject_count",
            "candidate_site_count",
            "existing_site_count",
            "snapped_demand_count",
            "snapped_site_count",
            "unsnapped_demand_count",
            "unsnapped_site_count",
        ):
            _require_non_negative_int(field_name, getattr(self, field_name))
        if (
            self.snapped_demand_count + self.unsnapped_demand_count
            != self.demand_subject_count
        ):
            raise InfrastructureNetworkSnapError(
                "snapped and unsnapped demand counts must equal demand subjects"
            )
        if (
            self.snapped_site_count + self.unsnapped_site_count
            != self.candidate_site_count + self.existing_site_count
        ):
            raise InfrastructureNetworkSnapError(
                "snapped and unsnapped site counts must equal site subjects"
            )


@dataclass(frozen=True, slots=True)
class InfrastructureNetworkSnapResult:
    network_snapshot_id: str
    working_srid: int
    max_snap_distance_m: float
    demand_snaps: tuple[InfrastructureDemandNetworkSnap, ...]
    site_snaps: tuple[InfrastructureSiteNetworkSnap, ...]
    unsnapped_demand_block_ids: tuple[str, ...]
    unsnapped_sites: tuple[InfrastructureUnsnappedSite, ...]
    diagnostics: InfrastructureNetworkSnapDiagnostics

    def __post_init__(self) -> None:
        _require_id("network_snapshot_id", self.network_snapshot_id)
        _require_positive_int("working_srid", self.working_srid)
        max_distance = require_max_distance_m(self.max_snap_distance_m)
        assert max_distance is not None
        if not isinstance(self.demand_snaps, tuple):
            raise InfrastructureNetworkSnapError(
                "demand_snaps must be an immutable tuple"
            )
        if not isinstance(self.site_snaps, tuple):
            raise InfrastructureNetworkSnapError(
                "site_snaps must be an immutable tuple"
            )
        if any(
            not isinstance(item, InfrastructureDemandNetworkSnap)
            for item in self.demand_snaps
        ):
            raise InfrastructureNetworkSnapError(
                "demand_snaps must contain InfrastructureDemandNetworkSnap values"
            )
        if any(
            not isinstance(item, InfrastructureSiteNetworkSnap)
            for item in self.site_snaps
        ):
            raise InfrastructureNetworkSnapError(
                "site_snaps must contain InfrastructureSiteNetworkSnap values"
            )
        demand_ids = tuple(item.block_id for item in self.demand_snaps)
        if demand_ids != tuple(sorted(demand_ids)) or len(demand_ids) != len(
            set(demand_ids)
        ):
            raise InfrastructureNetworkSnapError(
                "demand snaps must be sorted and unique by block_id"
            )
        site_keys = tuple(item.key for item in self.site_snaps)
        if site_keys != tuple(sorted(site_keys)) or len(site_keys) != len(
            set(site_keys)
        ):
            raise InfrastructureNetworkSnapError(
                "site snaps must be sorted and unique"
            )
        if not isinstance(self.unsnapped_demand_block_ids, tuple):
            raise InfrastructureNetworkSnapError(
                "unsnapped_demand_block_ids must be an immutable tuple"
            )
        if self.unsnapped_demand_block_ids != tuple(
            sorted(set(self.unsnapped_demand_block_ids))
        ):
            raise InfrastructureNetworkSnapError(
                "unsnapped demand ids must be sorted and unique"
            )
        if not isinstance(self.unsnapped_sites, tuple):
            raise InfrastructureNetworkSnapError(
                "unsnapped_sites must be an immutable tuple"
            )
        unsnapped_keys = tuple(item.key for item in self.unsnapped_sites)
        if unsnapped_keys != tuple(sorted(unsnapped_keys)) or len(
            unsnapped_keys
        ) != len(set(unsnapped_keys)):
            raise InfrastructureNetworkSnapError(
                "unsnapped sites must be sorted and unique"
            )
        if not isinstance(
            self.diagnostics,
            InfrastructureNetworkSnapDiagnostics,
        ):
            raise InfrastructureNetworkSnapError(
                "diagnostics must be InfrastructureNetworkSnapDiagnostics"
            )
        if self.diagnostics.snapped_demand_count != len(self.demand_snaps):
            raise InfrastructureNetworkSnapError(
                "diagnostic snapped_demand_count must match demand_snaps"
            )
        if self.diagnostics.snapped_site_count != len(self.site_snaps):
            raise InfrastructureNetworkSnapError(
                "diagnostic snapped_site_count must match site_snaps"
            )
        if self.diagnostics.unsnapped_demand_count != len(
            self.unsnapped_demand_block_ids
        ):
            raise InfrastructureNetworkSnapError(
                "diagnostic unsnapped_demand_count must match unsnapped ids"
            )
        if self.diagnostics.unsnapped_site_count != len(self.unsnapped_sites):
            raise InfrastructureNetworkSnapError(
                "diagnostic unsnapped_site_count must match unsnapped sites"
            )
        object.__setattr__(self, "max_snap_distance_m", max_distance)


class InfrastructureNetworkSnapper:
    """Snap infrastructure demand and facility sites to one immutable graph snapshot."""

    def __init__(
        self,
        backend: NetworkBackend,
        *,
        max_snap_distance_m: float,
        max_subjects: int = DEFAULT_MAX_INFRASTRUCTURE_SNAP_SUBJECTS,
    ) -> None:
        if not isinstance(backend, NetworkBackend):
            raise InfrastructureNetworkSnapError(
                "backend must satisfy NetworkBackend"
            )
        distance = require_max_distance_m(max_snap_distance_m)
        assert distance is not None
        _require_positive_int("max_subjects", max_subjects)
        self.backend = backend
        self.max_snap_distance_m = distance
        self.max_subjects = max_subjects

    def snap(
        self,
        demand: UnmetDemandResult,
        *,
        zoned_blocks: BlockZoneAssociationResult,
        candidate_geometries: tuple[
            InfrastructureCandidateGeometryResult, ...
        ] = (),
        existing: ExistingInfrastructureResult | None = None,
    ) -> InfrastructureNetworkSnapResult:
        if not isinstance(demand, UnmetDemandResult):
            raise InfrastructureNetworkSnapError(
                "demand must be an UnmetDemandResult"
            )
        if not isinstance(zoned_blocks, BlockZoneAssociationResult):
            raise InfrastructureNetworkSnapError(
                "zoned_blocks must be BlockZoneAssociationResult"
            )
        if not isinstance(candidate_geometries, tuple):
            raise InfrastructureNetworkSnapError(
                "candidate_geometries must be an immutable tuple"
            )
        if existing is not None and not isinstance(
            existing,
            ExistingInfrastructureResult,
        ):
            raise InfrastructureNetworkSnapError(
                "existing must be ExistingInfrastructureResult or None"
            )

        snapshot = self.backend.snapshot
        working_srid = snapshot.working_crs.srid
        if zoned_blocks.working_crs.srid != working_srid:
            raise InfrastructureNetworkSnapError(
                "block working SRID must match network snapshot"
            )

        block_points = self._demand_points(
            demand=demand,
            zoned_blocks=zoned_blocks,
        )
        candidate_sites = self._candidate_sites(
            candidate_geometries,
            working_srid=working_srid,
        )
        existing_sites = self._existing_sites(
            existing,
            working_srid=working_srid,
        )
        subject_count = (
            len(block_points) + len(candidate_sites) + len(existing_sites)
        )
        if subject_count > self.max_subjects:
            raise InfrastructureNetworkSnapError(
                "infrastructure snap subject limit exceeded: "
                f"{subject_count} > {self.max_subjects}"
            )

        demand_snaps: list[InfrastructureDemandNetworkSnap] = []
        unsnapped_demand: list[str] = []
        for block_id, point in block_points:
            match = self.backend.snap(
                point,
                max_distance_m=self.max_snap_distance_m,
            )
            if match is None:
                unsnapped_demand.append(block_id)
                continue
            demand_snaps.append(
                InfrastructureDemandNetworkSnap(
                    block_id=block_id,
                    point=point,
                    node=match.node,
                    snap_distance_m=match.distance_m,
                )
            )

        site_snaps: list[InfrastructureSiteNetworkSnap] = []
        unsnapped_sites: list[InfrastructureUnsnappedSite] = []
        for site_kind, site_id, type_code, point in (
            *candidate_sites,
            *existing_sites,
        ):
            match = self.backend.snap(
                point,
                max_distance_m=self.max_snap_distance_m,
            )
            if match is None:
                unsnapped_sites.append(
                    InfrastructureUnsnappedSite(
                        site_kind=site_kind,
                        site_id=site_id,
                        infrastructure_type_code=type_code,
                    )
                )
                continue
            site_snaps.append(
                InfrastructureSiteNetworkSnap(
                    site_kind=site_kind,
                    site_id=site_id,
                    infrastructure_type_code=type_code,
                    point=point,
                    node=match.node,
                    snap_distance_m=match.distance_m,
                )
            )

        ordered_demand = tuple(
            sorted(demand_snaps, key=lambda item: item.block_id)
        )
        ordered_sites = tuple(
            sorted(site_snaps, key=lambda item: item.key)
        )
        ordered_unsnapped_sites = tuple(
            sorted(unsnapped_sites, key=lambda item: item.key)
        )
        ordered_unsnapped_demand = tuple(sorted(unsnapped_demand))
        return InfrastructureNetworkSnapResult(
            network_snapshot_id=snapshot.snapshot_id,
            working_srid=working_srid,
            max_snap_distance_m=self.max_snap_distance_m,
            demand_snaps=ordered_demand,
            site_snaps=ordered_sites,
            unsnapped_demand_block_ids=ordered_unsnapped_demand,
            unsnapped_sites=ordered_unsnapped_sites,
            diagnostics=InfrastructureNetworkSnapDiagnostics(
                demand_subject_count=len(block_points),
                candidate_site_count=len(candidate_sites),
                existing_site_count=len(existing_sites),
                snapped_demand_count=len(ordered_demand),
                snapped_site_count=len(ordered_sites),
                unsnapped_demand_count=len(ordered_unsnapped_demand),
                unsnapped_site_count=len(ordered_unsnapped_sites),
            ),
        )

    @staticmethod
    def _demand_points(
        *,
        demand: UnmetDemandResult,
        zoned_blocks: BlockZoneAssociationResult,
    ) -> tuple[tuple[str, NetworkPoint], ...]:
        demanded_ids = {item.block_id for item in demand.demands}
        geometries: dict[str, Point] = {}
        for item in zoned_blocks.blocks:
            block_id = item.cleaned_block.block_id
            if block_id not in demanded_ids:
                continue
            if block_id in geometries:
                raise InfrastructureNetworkSnapError(
                    f"duplicate zoned block id: {block_id}"
                )
            geometries[block_id] = item.cleaned_block.geometry.representative_point()

        missing = demanded_ids - set(geometries)
        if missing:
            first = sorted(missing)[0]
            raise InfrastructureNetworkSnapError(
                f"demand block is missing from zoned blocks: {first}"
            )
        return tuple(
            (
                block_id,
                NetworkPoint(
                    x_m=float(geometries[block_id].x),
                    y_m=float(geometries[block_id].y),
                ),
            )
            for block_id in sorted(demanded_ids)
        )

    @staticmethod
    def _candidate_sites(
        results: tuple[InfrastructureCandidateGeometryResult, ...],
        *,
        working_srid: int,
    ) -> tuple[
        tuple[
            InfrastructureNetworkSiteKind,
            str,
            str,
            NetworkPoint,
        ],
        ...,
    ]:
        output: list[
            tuple[
                InfrastructureNetworkSiteKind,
                str,
                str,
                NetworkPoint,
            ]
        ] = []
        seen_ids: set[str] = set()
        for result in results:
            if not isinstance(result, InfrastructureCandidateGeometryResult):
                raise InfrastructureNetworkSnapError(
                    "candidate_geometries must contain "
                    "InfrastructureCandidateGeometryResult values"
                )
            if result.working_srid != working_srid:
                raise InfrastructureNetworkSnapError(
                    "candidate working SRID must match network snapshot"
                )
            for item in result.candidates:
                if item.candidate_id in seen_ids:
                    raise InfrastructureNetworkSnapError(
                        f"duplicate candidate site id: {item.candidate_id}"
                    )
                seen_ids.add(item.candidate_id)
                output.append(
                    (
                        InfrastructureNetworkSiteKind.CANDIDATE,
                        item.candidate_id,
                        item.infrastructure_type_code,
                        NetworkPoint(
                            x_m=float(item.anchor.x),
                            y_m=float(item.anchor.y),
                        ),
                    )
                )
        output.sort(key=lambda item: (item[0].value, item[1]))
        return tuple(output)

    @staticmethod
    def _existing_sites(
        existing: ExistingInfrastructureResult | None,
        *,
        working_srid: int,
    ) -> tuple[
        tuple[
            InfrastructureNetworkSiteKind,
            str,
            str,
            NetworkPoint,
        ],
        ...,
    ]:
        if existing is None:
            return ()
        output: list[
            tuple[
                InfrastructureNetworkSiteKind,
                str,
                str,
                NetworkPoint,
            ]
        ] = []
        seen_ids: set[str] = set()
        for item in existing.facilities:
            if item.working_srid != working_srid:
                raise InfrastructureNetworkSnapError(
                    "existing facility working SRID must match network snapshot"
                )
            if item.facility_id in seen_ids:
                raise InfrastructureNetworkSnapError(
                    f"duplicate existing facility id: {item.facility_id}"
                )
            seen_ids.add(item.facility_id)
            point = (
                item.geometry
                if isinstance(item.geometry, Point)
                else item.geometry.representative_point()
            )
            output.append(
                (
                    InfrastructureNetworkSiteKind.EXISTING,
                    item.facility_id,
                    item.infrastructure_type_code,
                    NetworkPoint(
                        x_m=float(point.x),
                        y_m=float(point.y),
                    ),
                )
            )
        output.sort(key=lambda item: (item[0].value, item[1]))
        return tuple(output)


def _require_id(field_name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise InfrastructureNetworkSnapError(
            f"{field_name} must be a non-empty string"
        )
    if "\n" in value or "\r" in value:
        raise InfrastructureNetworkSnapError(
            f"{field_name} must not contain line breaks"
        )


def _require_positive_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise InfrastructureNetworkSnapError(
            f"{field_name} must be a positive integer"
        )


def _require_non_negative_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise InfrastructureNetworkSnapError(
            f"{field_name} must be a non-negative integer"
        )


def _require_non_negative_finite(
    field_name: str,
    value: int | float,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InfrastructureNetworkSnapError(
            f"{field_name} must be a finite non-negative number"
        )
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        raise InfrastructureNetworkSnapError(
            f"{field_name} must be a finite non-negative number"
        )
    return number
