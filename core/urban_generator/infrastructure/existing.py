from __future__ import annotations

import math
from dataclasses import dataclass

from shapely.geometry.base import BaseGeometry

from core.urban_generator.domain import SnapshotLayerKind, TerritorySnapshot
from core.urban_generator.infrastructure.config import InfrastructureType


class ExistingInfrastructureError(ValueError):
    """Raised when S10-T02 existing infrastructure input violates its contract."""


@dataclass(frozen=True, slots=True)
class ExistingFacilitySourceRecord:
    """DB-independent normalized facility record tied to one snapshot layer."""

    source_ref: str
    source_feature_id: str
    facility_use: str
    geometry: BaseGeometry
    working_srid: int
    capacity: float | None = None
    name: str | None = None

    def __post_init__(self) -> None:
        _require_non_empty("source_ref", self.source_ref)
        _require_non_empty("source_feature_id", self.source_feature_id)
        _require_non_empty("facility_use", self.facility_use)
        _require_positive_int("working_srid", self.working_srid)
        if not isinstance(self.geometry, BaseGeometry):
            raise ExistingInfrastructureError(
                "geometry must be a Shapely BaseGeometry"
            )
        if self.geometry.is_empty:
            raise ExistingInfrastructureError("geometry must not be empty")
        if self.capacity is not None:
            capacity = _require_positive_finite("capacity", self.capacity)
            object.__setattr__(self, "capacity", capacity)
        if self.name is not None:
            _require_non_empty("name", self.name)


@dataclass(frozen=True, slots=True)
class ExistingFacilityMappingRule:
    """Map one source facility use to one InfrastructureType code."""

    facility_use: str
    infrastructure_type_code: str
    default_capacity: float | None = None

    def __post_init__(self) -> None:
        _require_non_empty("facility_use", self.facility_use)
        _require_non_empty(
            "infrastructure_type_code",
            self.infrastructure_type_code,
        )
        if self.default_capacity is not None:
            capacity = _require_positive_finite(
                "default_capacity",
                self.default_capacity,
            )
            object.__setattr__(self, "default_capacity", capacity)


@dataclass(frozen=True, slots=True)
class ExistingInfrastructureFacility:
    """Typed fixed facility available before generated placement begins."""

    facility_id: str
    source_ref: str
    source_feature_id: str
    infrastructure_type_code: str
    capacity: float
    geometry: BaseGeometry
    working_srid: int
    name: str | None = None

    def __post_init__(self) -> None:
        _require_non_empty("facility_id", self.facility_id)
        _require_non_empty("source_ref", self.source_ref)
        _require_non_empty("source_feature_id", self.source_feature_id)
        _require_non_empty(
            "infrastructure_type_code",
            self.infrastructure_type_code,
        )
        capacity = _require_positive_finite("capacity", self.capacity)
        _require_positive_int("working_srid", self.working_srid)
        if not isinstance(self.geometry, BaseGeometry) or self.geometry.is_empty:
            raise ExistingInfrastructureError(
                "geometry must be a non-empty Shapely geometry"
            )
        if self.name is not None:
            _require_non_empty("name", self.name)
        object.__setattr__(self, "capacity", capacity)


@dataclass(frozen=True, slots=True)
class ExistingInfrastructureDiagnostics:
    input_count: int
    mapped_count: int
    skipped_unmapped_count: int
    source_layer_count: int
    infrastructure_type_count: int

    def __post_init__(self) -> None:
        for field_name in (
            "input_count",
            "mapped_count",
            "skipped_unmapped_count",
            "source_layer_count",
            "infrastructure_type_count",
        ):
            _require_non_negative_int(field_name, getattr(self, field_name))
        if self.mapped_count + self.skipped_unmapped_count != self.input_count:
            raise ExistingInfrastructureError(
                "mapped and skipped counts must equal input_count"
            )


@dataclass(frozen=True, slots=True)
class ExistingInfrastructureResult:
    snapshot_id: str
    facilities: tuple[ExistingInfrastructureFacility, ...]
    diagnostics: ExistingInfrastructureDiagnostics

    def __post_init__(self) -> None:
        _require_non_empty("snapshot_id", self.snapshot_id)
        if not isinstance(self.facilities, tuple):
            raise ExistingInfrastructureError(
                "facilities must be an immutable tuple"
            )
        if any(
            not isinstance(item, ExistingInfrastructureFacility)
            for item in self.facilities
        ):
            raise ExistingInfrastructureError(
                "facilities must contain ExistingInfrastructureFacility values"
            )
        ids = tuple(item.facility_id for item in self.facilities)
        if ids != tuple(sorted(ids)) or len(ids) != len(set(ids)):
            raise ExistingInfrastructureError(
                "facilities must be sorted and have unique facility ids"
            )
        if not isinstance(
            self.diagnostics,
            ExistingInfrastructureDiagnostics,
        ):
            raise ExistingInfrastructureError(
                "diagnostics must be ExistingInfrastructureDiagnostics"
            )
        if self.diagnostics.mapped_count != len(self.facilities):
            raise ExistingInfrastructureError(
                "diagnostic mapped_count must match facilities"
            )


class ExistingInfrastructureAdapter:
    """Normalize fixed facilities referenced by a TerritorySnapshot."""

    def adapt(
        self,
        snapshot: TerritorySnapshot,
        *,
        records: tuple[ExistingFacilitySourceRecord, ...],
        infrastructure_types: tuple[InfrastructureType, ...],
        mapping_rules: tuple[ExistingFacilityMappingRule, ...],
        skip_unmapped: bool = True,
    ) -> ExistingInfrastructureResult:
        if not isinstance(snapshot, TerritorySnapshot):
            raise ExistingInfrastructureError(
                "snapshot must be a TerritorySnapshot"
            )
        if not isinstance(records, tuple):
            raise ExistingInfrastructureError(
                "records must be an immutable tuple"
            )
        if not isinstance(infrastructure_types, tuple):
            raise ExistingInfrastructureError(
                "infrastructure_types must be an immutable tuple"
            )
        if not isinstance(mapping_rules, tuple):
            raise ExistingInfrastructureError(
                "mapping_rules must be an immutable tuple"
            )
        if not isinstance(skip_unmapped, bool):
            raise ExistingInfrastructureError("skip_unmapped must be boolean")

        types_by_code = self._types_by_code(infrastructure_types)
        rules_by_use = self._rules_by_use(mapping_rules)
        allowed_source_refs = self._facility_source_refs(snapshot)

        facilities: list[ExistingInfrastructureFacility] = []
        skipped = 0
        seen_source_ids: set[tuple[str, str]] = set()

        for record in sorted(
            records,
            key=lambda item: (item.source_ref, item.source_feature_id),
        ):
            if not isinstance(record, ExistingFacilitySourceRecord):
                raise ExistingInfrastructureError(
                    "records must contain ExistingFacilitySourceRecord values"
                )
            if record.source_ref not in allowed_source_refs:
                raise ExistingInfrastructureError(
                    "facility record source_ref is not part of snapshot facilities: "
                    f"{record.source_ref}"
                )
            if record.working_srid != snapshot.settings.working_srid:
                raise ExistingInfrastructureError(
                    "facility working_srid must match snapshot working_srid"
                )
            source_key = (record.source_ref, record.source_feature_id)
            if source_key in seen_source_ids:
                raise ExistingInfrastructureError(
                    "duplicate facility source feature: "
                    f"{record.source_ref}:{record.source_feature_id}"
                )
            seen_source_ids.add(source_key)

            rule = rules_by_use.get(record.facility_use)
            if rule is None:
                if skip_unmapped:
                    skipped += 1
                    continue
                raise ExistingInfrastructureError(
                    f"no facility mapping rule for use {record.facility_use!r}"
                )

            infrastructure_type = types_by_code.get(
                rule.infrastructure_type_code
            )
            if infrastructure_type is None:
                raise ExistingInfrastructureError(
                    "facility mapping references unknown infrastructure type: "
                    f"{rule.infrastructure_type_code}"
                )
            capacity = (
                record.capacity
                if record.capacity is not None
                else (
                    rule.default_capacity
                    if rule.default_capacity is not None
                    else infrastructure_type.capacity
                )
            )
            facilities.append(
                ExistingInfrastructureFacility(
                    facility_id=(
                        f"{record.source_ref}:{record.source_feature_id}"
                    ),
                    source_ref=record.source_ref,
                    source_feature_id=record.source_feature_id,
                    infrastructure_type_code=infrastructure_type.code,
                    capacity=capacity,
                    geometry=record.geometry,
                    working_srid=record.working_srid,
                    name=record.name,
                )
            )

        ordered = tuple(
            sorted(facilities, key=lambda item: item.facility_id)
        )
        return ExistingInfrastructureResult(
            snapshot_id=str(snapshot.snapshot_id),
            facilities=ordered,
            diagnostics=ExistingInfrastructureDiagnostics(
                input_count=len(records),
                mapped_count=len(ordered),
                skipped_unmapped_count=skipped,
                source_layer_count=len(allowed_source_refs),
                infrastructure_type_count=len(types_by_code),
            ),
        )

    @staticmethod
    def _types_by_code(
        infrastructure_types: tuple[InfrastructureType, ...],
    ) -> dict[str, InfrastructureType]:
        by_code: dict[str, InfrastructureType] = {}
        for item in infrastructure_types:
            if not isinstance(item, InfrastructureType):
                raise ExistingInfrastructureError(
                    "infrastructure_types must contain InfrastructureType values"
                )
            if item.code in by_code:
                raise ExistingInfrastructureError(
                    f"duplicate infrastructure type code: {item.code}"
                )
            by_code[item.code] = item
        return by_code

    @staticmethod
    def _rules_by_use(
        mapping_rules: tuple[ExistingFacilityMappingRule, ...],
    ) -> dict[str, ExistingFacilityMappingRule]:
        by_use: dict[str, ExistingFacilityMappingRule] = {}
        for item in mapping_rules:
            if not isinstance(item, ExistingFacilityMappingRule):
                raise ExistingInfrastructureError(
                    "mapping_rules must contain ExistingFacilityMappingRule values"
                )
            if item.facility_use in by_use:
                raise ExistingInfrastructureError(
                    f"duplicate facility mapping use: {item.facility_use}"
                )
            by_use[item.facility_use] = item
        return by_use

    @staticmethod
    def _facility_source_refs(snapshot: TerritorySnapshot) -> set[str]:
        refs: set[str] = set()
        for item in snapshot.facilities:
            if item.kind is not SnapshotLayerKind.FACILITIES:
                raise ExistingInfrastructureError(
                    "snapshot facilities must contain FACILITIES refs"
                )
            refs.add(item.source_ref)
        return refs


def _require_non_empty(field_name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ExistingInfrastructureError(
            f"{field_name} must be a non-empty string"
        )


def _require_positive_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ExistingInfrastructureError(
            f"{field_name} must be a positive integer"
        )


def _require_non_negative_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ExistingInfrastructureError(
            f"{field_name} must be a non-negative integer"
        )


def _require_positive_finite(
    field_name: str,
    value: int | float,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ExistingInfrastructureError(
            f"{field_name} must be a finite positive number"
        )
    number = float(value)
    if not math.isfinite(number) or number <= 0.0:
        raise ExistingInfrastructureError(
            f"{field_name} must be a finite positive number"
        )
    return number
