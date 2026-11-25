from __future__ import annotations

import math
from dataclasses import dataclass

from core.urban_generator.demography import (
    DemographicDemandCategory,
    DemographicDemandProfile,
)
from core.urban_generator.infrastructure.config import (
    InfrastructureCategory,
    InfrastructureType,
)
from core.urban_generator.infrastructure.existing import (
    ExistingInfrastructureResult,
)
from core.urban_generator.zoning import ZoneClass

DEFAULT_MAX_UNMET_DEMAND_ITEMS = 1_000_000


class UnmetDemandError(ValueError):
    """Raised when S10-T03 demand inputs violate the infrastructure contract."""


@dataclass(frozen=True, slots=True)
class InfrastructureServedDemand:
    """Explicit covered demand supplied by a later accessibility/placement stage."""

    block_id: str
    infrastructure_type_code: str
    value: float

    def __post_init__(self) -> None:
        _require_id("block_id", self.block_id)
        _require_id(
            "infrastructure_type_code",
            self.infrastructure_type_code,
        )
        value = _require_non_negative_finite("value", self.value)
        object.__setattr__(self, "value", value)


@dataclass(frozen=True, slots=True)
class BlockInfrastructureDemand:
    """Gross, served and remaining demand for one block/type pair."""

    block_id: str
    zone_id: str
    zone_class: ZoneClass
    infrastructure_type_code: str
    infrastructure_category: InfrastructureCategory
    demographic_signal: DemographicDemandCategory
    demographic_group: str | None
    source_signal_value: float
    demand_rate: float
    gross_demand: float
    served_demand: float
    unmet_demand: float

    def __post_init__(self) -> None:
        _require_id("block_id", self.block_id)
        _require_id("zone_id", self.zone_id)
        if not isinstance(self.zone_class, ZoneClass):
            raise UnmetDemandError("zone_class must be a ZoneClass")
        _require_id(
            "infrastructure_type_code",
            self.infrastructure_type_code,
        )
        if not isinstance(
            self.infrastructure_category,
            InfrastructureCategory,
        ):
            raise UnmetDemandError(
                "infrastructure_category must be InfrastructureCategory"
            )
        if not isinstance(
            self.demographic_signal,
            DemographicDemandCategory,
        ):
            raise UnmetDemandError(
                "demographic_signal must be DemographicDemandCategory"
            )
        if self.demographic_signal is DemographicDemandCategory.AGE_GROUP:
            _require_id("demographic_group", self.demographic_group)
        elif self.demographic_group is not None:
            raise UnmetDemandError(
                "demographic_group is only valid for age_group demand"
            )
        source = _require_non_negative_finite(
            "source_signal_value",
            self.source_signal_value,
        )
        rate = _require_positive_finite("demand_rate", self.demand_rate)
        gross = _require_non_negative_finite(
            "gross_demand",
            self.gross_demand,
        )
        served = _require_non_negative_finite(
            "served_demand",
            self.served_demand,
        )
        unmet = _require_non_negative_finite(
            "unmet_demand",
            self.unmet_demand,
        )
        expected_gross = source * rate
        if not math.isclose(
            gross,
            expected_gross,
            rel_tol=1e-12,
            abs_tol=1e-9,
        ):
            raise UnmetDemandError(
                "gross_demand must equal source_signal_value * demand_rate"
            )
        if served > gross and not math.isclose(
            served,
            gross,
            rel_tol=1e-12,
            abs_tol=1e-9,
        ):
            raise UnmetDemandError(
                "served_demand cannot exceed gross_demand"
            )
        expected_unmet = max(gross - served, 0.0)
        if not math.isclose(
            unmet,
            expected_unmet,
            rel_tol=1e-12,
            abs_tol=1e-9,
        ):
            raise UnmetDemandError(
                "unmet_demand must equal gross_demand - served_demand"
            )
        object.__setattr__(self, "source_signal_value", source)
        object.__setattr__(self, "demand_rate", rate)
        object.__setattr__(self, "gross_demand", gross)
        object.__setattr__(self, "served_demand", min(served, gross))
        object.__setattr__(self, "unmet_demand", expected_unmet)

    @property
    def key(self) -> tuple[str, str]:
        return self.block_id, self.infrastructure_type_code


@dataclass(frozen=True, slots=True)
class InfrastructureDemandSummary:
    infrastructure_type_code: str
    infrastructure_category: InfrastructureCategory
    demographic_signal: DemographicDemandCategory
    demographic_group: str | None
    gross_demand: float
    served_demand: float
    unmet_demand: float
    existing_capacity: float

    def __post_init__(self) -> None:
        _require_id(
            "infrastructure_type_code",
            self.infrastructure_type_code,
        )
        if not isinstance(
            self.infrastructure_category,
            InfrastructureCategory,
        ):
            raise UnmetDemandError(
                "infrastructure_category must be InfrastructureCategory"
            )
        if not isinstance(
            self.demographic_signal,
            DemographicDemandCategory,
        ):
            raise UnmetDemandError(
                "demographic_signal must be DemographicDemandCategory"
            )
        if self.demographic_signal is DemographicDemandCategory.AGE_GROUP:
            _require_id("demographic_group", self.demographic_group)
        elif self.demographic_group is not None:
            raise UnmetDemandError(
                "demographic_group is only valid for age_group demand"
            )
        gross = _require_non_negative_finite(
            "gross_demand",
            self.gross_demand,
        )
        served = _require_non_negative_finite(
            "served_demand",
            self.served_demand,
        )
        unmet = _require_non_negative_finite(
            "unmet_demand",
            self.unmet_demand,
        )
        capacity = _require_non_negative_finite(
            "existing_capacity",
            self.existing_capacity,
        )
        if not math.isclose(
            gross - served,
            unmet,
            rel_tol=1e-12,
            abs_tol=1e-9,
        ):
            raise UnmetDemandError(
                "summary gross - served must equal unmet"
            )
        object.__setattr__(self, "gross_demand", gross)
        object.__setattr__(self, "served_demand", served)
        object.__setattr__(self, "unmet_demand", unmet)
        object.__setattr__(self, "existing_capacity", capacity)


@dataclass(frozen=True, slots=True)
class UnmetDemandDiagnostics:
    block_count: int
    infrastructure_type_count: int
    demand_item_count: int
    served_assignment_count: int
    existing_facility_count: int

    def __post_init__(self) -> None:
        for field_name in (
            "block_count",
            "infrastructure_type_count",
            "demand_item_count",
            "served_assignment_count",
            "existing_facility_count",
        ):
            _require_non_negative_int(field_name, getattr(self, field_name))
        if (
            self.demand_item_count
            != self.block_count * self.infrastructure_type_count
        ):
            raise UnmetDemandError(
                "demand_item_count must equal blocks * infrastructure types"
            )


@dataclass(frozen=True, slots=True)
class UnmetDemandResult:
    scenario_version: str
    scenario_fingerprint: str
    demands: tuple[BlockInfrastructureDemand, ...]
    summaries: tuple[InfrastructureDemandSummary, ...]
    diagnostics: UnmetDemandDiagnostics

    def __post_init__(self) -> None:
        _require_id("scenario_version", self.scenario_version)
        _require_sha256("scenario_fingerprint", self.scenario_fingerprint)
        if not isinstance(self.demands, tuple):
            raise UnmetDemandError("demands must be an immutable tuple")
        if any(
            not isinstance(item, BlockInfrastructureDemand)
            for item in self.demands
        ):
            raise UnmetDemandError(
                "demands must contain BlockInfrastructureDemand values"
            )
        keys = tuple(item.key for item in self.demands)
        if keys != tuple(sorted(keys)) or len(keys) != len(set(keys)):
            raise UnmetDemandError(
                "demands must be canonically sorted and unique"
            )
        if not isinstance(self.summaries, tuple):
            raise UnmetDemandError("summaries must be an immutable tuple")
        if any(
            not isinstance(item, InfrastructureDemandSummary)
            for item in self.summaries
        ):
            raise UnmetDemandError(
                "summaries must contain InfrastructureDemandSummary values"
            )
        summary_codes = tuple(
            item.infrastructure_type_code for item in self.summaries
        )
        if summary_codes != tuple(sorted(summary_codes)) or len(
            summary_codes
        ) != len(set(summary_codes)):
            raise UnmetDemandError(
                "summaries must be canonically sorted and unique"
            )
        if not isinstance(self.diagnostics, UnmetDemandDiagnostics):
            raise UnmetDemandError(
                "diagnostics must be UnmetDemandDiagnostics"
            )
        if self.diagnostics.demand_item_count != len(self.demands):
            raise UnmetDemandError(
                "diagnostic demand_item_count must match demands"
            )
        if self.diagnostics.infrastructure_type_count != len(self.summaries):
            raise UnmetDemandError(
                "diagnostic infrastructure_type_count must match summaries"
            )
        _validate_summaries(self.demands, self.summaries)


class UnmetDemandCalculator:
    """Calculate typed block demand without assuming accessibility before T07."""

    def __init__(
        self,
        *,
        max_demand_items: int = DEFAULT_MAX_UNMET_DEMAND_ITEMS,
    ) -> None:
        _require_positive_int("max_demand_items", max_demand_items)
        self.max_demand_items = max_demand_items

    def calculate(
        self,
        profile: DemographicDemandProfile,
        *,
        infrastructure_types: tuple[InfrastructureType, ...],
        existing: ExistingInfrastructureResult | None = None,
        served: tuple[InfrastructureServedDemand, ...] = (),
    ) -> UnmetDemandResult:
        if not isinstance(profile, DemographicDemandProfile):
            raise UnmetDemandError(
                "profile must be a DemographicDemandProfile"
            )
        if not isinstance(infrastructure_types, tuple):
            raise UnmetDemandError(
                "infrastructure_types must be an immutable tuple"
            )
        if not isinstance(served, tuple):
            raise UnmetDemandError(
                "served must be an immutable tuple"
            )
        if existing is not None and not isinstance(
            existing,
            ExistingInfrastructureResult,
        ):
            raise UnmetDemandError(
                "existing must be ExistingInfrastructureResult or None"
            )

        types = self._canonical_types(infrastructure_types)
        work_items = len(profile.blocks) * len(types)
        if work_items > self.max_demand_items:
            raise UnmetDemandError(
                "unmet-demand work item limit exceeded: "
                f"{work_items} > {self.max_demand_items}"
            )
        served_by_key = self._served_by_key(served)
        valid_keys = {
            (block.block_id, item.code)
            for block in profile.blocks
            for item in types
        }
        unknown_served = set(served_by_key) - valid_keys
        if unknown_served:
            first = sorted(unknown_served)[0]
            raise UnmetDemandError(
                "served demand references unknown block/type pair: "
                f"{first[0]}/{first[1]}"
            )

        demands: list[BlockInfrastructureDemand] = []
        for block in profile.blocks:
            for item in types:
                model = item.demand_model
                signal = block.signal(
                    model.signal,
                    demographic_group=model.demographic_group,
                )
                gross = signal.value * model.demand_rate
                served_value = served_by_key.get(
                    (block.block_id, item.code),
                    0.0,
                )
                if served_value > gross and not math.isclose(
                    served_value,
                    gross,
                    rel_tol=1e-12,
                    abs_tol=1e-9,
                ):
                    raise UnmetDemandError(
                        "served demand exceeds gross demand for "
                        f"{block.block_id}/{item.code}"
                    )
                demands.append(
                    BlockInfrastructureDemand(
                        block_id=block.block_id,
                        zone_id=block.zone_id,
                        zone_class=block.zone_class,
                        infrastructure_type_code=item.code,
                        infrastructure_category=item.category,
                        demographic_signal=model.signal,
                        demographic_group=model.demographic_group,
                        source_signal_value=signal.value,
                        demand_rate=model.demand_rate,
                        gross_demand=gross,
                        served_demand=min(served_value, gross),
                        unmet_demand=max(gross - served_value, 0.0),
                    )
                )

        ordered_demands = tuple(
            sorted(
                demands,
                key=lambda item: (
                    item.block_id,
                    item.infrastructure_type_code,
                ),
            )
        )
        capacity_by_type = self._existing_capacity(
            existing=existing,
            types=types,
        )
        summaries = tuple(
            self._summary(
                item,
                demands=ordered_demands,
                existing_capacity=capacity_by_type.get(item.code, 0.0),
            )
            for item in types
        )
        return UnmetDemandResult(
            scenario_version=profile.scenario_version,
            scenario_fingerprint=profile.scenario_fingerprint,
            demands=ordered_demands,
            summaries=summaries,
            diagnostics=UnmetDemandDiagnostics(
                block_count=len(profile.blocks),
                infrastructure_type_count=len(types),
                demand_item_count=len(ordered_demands),
                served_assignment_count=len(served),
                existing_facility_count=(
                    len(existing.facilities) if existing is not None else 0
                ),
            ),
        )

    @staticmethod
    def _canonical_types(
        infrastructure_types: tuple[InfrastructureType, ...],
    ) -> tuple[InfrastructureType, ...]:
        by_code: dict[str, InfrastructureType] = {}
        for item in infrastructure_types:
            if not isinstance(item, InfrastructureType):
                raise UnmetDemandError(
                    "infrastructure_types must contain InfrastructureType values"
                )
            if item.code in by_code:
                raise UnmetDemandError(
                    f"duplicate infrastructure type code: {item.code}"
                )
            by_code[item.code] = item
        return tuple(by_code[code] for code in sorted(by_code))

    @staticmethod
    def _served_by_key(
        served: tuple[InfrastructureServedDemand, ...],
    ) -> dict[tuple[str, str], float]:
        by_key: dict[tuple[str, str], float] = {}
        for item in served:
            if not isinstance(item, InfrastructureServedDemand):
                raise UnmetDemandError(
                    "served must contain InfrastructureServedDemand values"
                )
            key = (item.block_id, item.infrastructure_type_code)
            if key in by_key:
                raise UnmetDemandError(
                    "duplicate served demand assignment for "
                    f"{item.block_id}/{item.infrastructure_type_code}"
                )
            by_key[key] = item.value
        return by_key

    @staticmethod
    def _existing_capacity(
        *,
        existing: ExistingInfrastructureResult | None,
        types: tuple[InfrastructureType, ...],
    ) -> dict[str, float]:
        if existing is None:
            return {}
        valid_codes = {item.code for item in types}
        totals: dict[str, float] = {}
        for facility in existing.facilities:
            if facility.infrastructure_type_code not in valid_codes:
                raise UnmetDemandError(
                    "existing facility references infrastructure type "
                    "outside demand calculation: "
                    f"{facility.infrastructure_type_code}"
                )
            totals[facility.infrastructure_type_code] = (
                totals.get(facility.infrastructure_type_code, 0.0)
                + facility.capacity
            )
        return totals

    @staticmethod
    def _summary(
        infrastructure_type: InfrastructureType,
        *,
        demands: tuple[BlockInfrastructureDemand, ...],
        existing_capacity: float,
    ) -> InfrastructureDemandSummary:
        items = tuple(
            item
            for item in demands
            if item.infrastructure_type_code == infrastructure_type.code
        )
        model = infrastructure_type.demand_model
        return InfrastructureDemandSummary(
            infrastructure_type_code=infrastructure_type.code,
            infrastructure_category=infrastructure_type.category,
            demographic_signal=model.signal,
            demographic_group=model.demographic_group,
            gross_demand=math.fsum(item.gross_demand for item in items),
            served_demand=math.fsum(item.served_demand for item in items),
            unmet_demand=math.fsum(item.unmet_demand for item in items),
            existing_capacity=existing_capacity,
        )


def _validate_summaries(
    demands: tuple[BlockInfrastructureDemand, ...],
    summaries: tuple[InfrastructureDemandSummary, ...],
) -> None:
    for summary in summaries:
        items = tuple(
            item
            for item in demands
            if item.infrastructure_type_code
            == summary.infrastructure_type_code
        )
        gross = math.fsum(item.gross_demand for item in items)
        served = math.fsum(item.served_demand for item in items)
        unmet = math.fsum(item.unmet_demand for item in items)
        if not math.isclose(
            gross,
            summary.gross_demand,
            rel_tol=1e-12,
            abs_tol=1e-9,
        ):
            raise UnmetDemandError(
                "summary gross demand must equal block demand total"
            )
        if not math.isclose(
            served,
            summary.served_demand,
            rel_tol=1e-12,
            abs_tol=1e-9,
        ):
            raise UnmetDemandError(
                "summary served demand must equal block served total"
            )
        if not math.isclose(
            unmet,
            summary.unmet_demand,
            rel_tol=1e-12,
            abs_tol=1e-9,
        ):
            raise UnmetDemandError(
                "summary unmet demand must equal block unmet total"
            )


def _require_id(field_name: str, value: str | None) -> None:
    if not isinstance(value, str) or not value.strip():
        raise UnmetDemandError(
            f"{field_name} must be a non-empty string"
        )


def _require_sha256(field_name: str, value: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(ch not in "0123456789abcdef" for ch in value)
    ):
        raise UnmetDemandError(
            f"{field_name} must be a lowercase SHA-256 hex digest"
        )


def _require_positive_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise UnmetDemandError(
            f"{field_name} must be a positive integer"
        )


def _require_non_negative_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise UnmetDemandError(
            f"{field_name} must be a non-negative integer"
        )


def _require_non_negative_finite(
    field_name: str,
    value: int | float,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise UnmetDemandError(
            f"{field_name} must be a finite non-negative number"
        )
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        raise UnmetDemandError(
            f"{field_name} must be a finite non-negative number"
        )
    return number


def _require_positive_finite(
    field_name: str,
    value: int | float,
) -> float:
    number = _require_non_negative_finite(field_name, value)
    if number <= 0.0:
        raise UnmetDemandError(
            f"{field_name} must be greater than zero"
        )
    return number
