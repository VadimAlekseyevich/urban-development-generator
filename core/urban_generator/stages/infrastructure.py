"""Canonical infrastructure Stage composed from the existing S10 algorithms."""

from __future__ import annotations

from dataclasses import dataclass, replace

from shapely.geometry.base import BaseGeometry
from shapely.strtree import STRtree

from core.urban_generator.blocks import BlockZoneAssociationResult, PlanningParcel
from core.urban_generator.buildings import BuildingAreaSubject
from core.urban_generator.demography import DemographicDemandProfile
from core.urban_generator.domain import (
    NetworkBackend,
    NetworkPoint,
    RunContext,
    RunMode,
    StageDiagnostic,
    StageFingerprintWriter,
    StageResult,
    TerritorySnapshot,
    require_stage_input,
)
from core.urban_generator.infrastructure import (
    ExistingFacilityMappingRule,
    ExistingFacilitySourceRecord,
    ExistingInfrastructureAdapter,
    ExistingInfrastructureFacilityRef,
    ExistingInfrastructureFacilitySnapInput,
    ExistingInfrastructureResult,
    InfrastructureAccessibilityBatchResult,
    InfrastructureCandidateAccessibilityPolicy,
    InfrastructureCandidateGeometry,
    InfrastructureCandidateGeometryBuilder,
    InfrastructureCandidateGeometryResult,
    InfrastructureCandidateRef,
    InfrastructureCandidateSiteGenerator,
    InfrastructureCandidateSnapInput,
    InfrastructureDemandRef,
    InfrastructureDemandSnapInput,
    InfrastructureFeasibilityRejectionReason,
    InfrastructureFeasibilityResult,
    InfrastructureGreedyPlacementPolicy,
    InfrastructureGreedyPlacementState,
    InfrastructureMetricsBuilder,
    InfrastructureMetricsResult,
    InfrastructureNetworkSnapBatchResult,
    InfrastructureNetworkSnapInput,
    InfrastructureNetworkSnapPolicy,
    InfrastructurePlacementSelectionStatus,
    InfrastructureServedDemand,
    InfrastructureType,
    UnmetDemandCalculator,
    UnmetDemandResult,
    apply_infrastructure_greedy_selection,
    calculate_infrastructure_candidate_benefits,
    compute_candidate_site_accessibility_batch,
    compute_existing_facility_accessibility_batch,
    initialize_infrastructure_greedy_placement_state,
    select_infrastructure_greedy_candidate,
    snap_infrastructure_network_batch,
    validate_infrastructure_candidate_feasibility,
)
from core.urban_generator.stages.catalog import (
    DEMOGRAPHY_STAGE,
    INFRASTRUCTURE_STAGE,
    ROADS_STAGE,
)

_MAX_TYPES = 16
_MAX_SPATIAL_INPUTS = 100_000


@dataclass(frozen=True, slots=True)
class InfrastructureStageConfig:
    infrastructure_types: tuple[InfrastructureType, ...]
    snap_policy: InfrastructureNetworkSnapPolicy
    mapping_rules: tuple[ExistingFacilityMappingRule, ...] = ()
    candidate_policy: InfrastructureCandidateAccessibilityPolicy = (
        InfrastructureCandidateAccessibilityPolicy()
    )
    placement_policy: InfrastructureGreedyPlacementPolicy = InfrastructureGreedyPlacementPolicy(
        max_facilities=200, max_iterations=200,
    )
    max_spatial_inputs: int = _MAX_SPATIAL_INPUTS
    skip_unmapped: bool = False

    def __post_init__(self) -> None:
        if (
            not isinstance(self.infrastructure_types, tuple)
            or not 1 <= len(self.infrastructure_types) <= _MAX_TYPES
            or any(not isinstance(item, InfrastructureType) for item in self.infrastructure_types)
        ):
            raise ValueError("infrastructure requires 1..16 explicit types")
        codes = tuple(item.code for item in self.infrastructure_types)
        if len(codes) != len(set(codes)):
            raise ValueError("infrastructure type codes must be unique")
        if not isinstance(self.mapping_rules, tuple) or any(
            not isinstance(item, ExistingFacilityMappingRule) for item in self.mapping_rules
        ):
            raise ValueError("invalid existing facility mapping rules")
        if not isinstance(self.snap_policy, InfrastructureNetworkSnapPolicy):
            raise TypeError("invalid infrastructure snap policy")
        if not isinstance(self.candidate_policy, InfrastructureCandidateAccessibilityPolicy):
            raise TypeError("invalid candidate accessibility policy")
        if not isinstance(self.placement_policy, InfrastructureGreedyPlacementPolicy):
            raise TypeError("invalid greedy placement policy")
        if type(self.max_spatial_inputs) is not int or not (
            1 <= self.max_spatial_inputs <= _MAX_SPATIAL_INPUTS
        ):
            raise ValueError("infrastructure spatial input limit out of range")
        if type(self.skip_unmapped) is not bool:
            raise ValueError("skip_unmapped must be boolean")


@dataclass(frozen=True, slots=True)
class InfrastructureStageInput:
    demand_profile: DemographicDemandProfile
    network: NetworkBackend
    blocks: BlockZoneAssociationResult
    parcels: tuple[PlanningParcel, ...] = ()
    buildings: tuple[BuildingAreaSubject, ...] = ()
    fixed_facilities: tuple[ExistingFacilitySourceRecord, ...] = ()
    fixed_building_footprints: tuple[BaseGeometry, ...] = ()


@dataclass(frozen=True, slots=True)
class InfrastructureTypeStageOutput:
    infrastructure_type: InfrastructureType
    candidate_geometry: InfrastructureCandidateGeometryResult
    snaps: InfrastructureNetworkSnapBatchResult
    existing_accessibility: InfrastructureAccessibilityBatchResult
    candidate_accessibility: InfrastructureAccessibilityBatchResult
    placement: InfrastructureGreedyPlacementState


@dataclass(frozen=True, slots=True)
class InfrastructureStageOutput:
    existing: ExistingInfrastructureResult
    demand: UnmetDemandResult
    types: tuple[InfrastructureTypeStageOutput, ...]
    metrics: InfrastructureMetricsResult


def _point(geometry: BaseGeometry) -> NetworkPoint:
    anchor = geometry.representative_point()
    return NetworkPoint(x_m=float(anchor.x), y_m=float(anchor.y))


class InfrastructureStage:
    """Resolve bounded network service and generated facilities without persistence."""

    name = INFRASTRUCTURE_STAGE
    version = "1.0.0"
    dependencies = (DEMOGRAPHY_STAGE, ROADS_STAGE)

    def validate_input(self, value: object) -> InfrastructureStageInput:
        return require_stage_input(value, InfrastructureStageInput, stage_name=self.name)

    def execute(
        self, *, snapshot: TerritorySnapshot, context: RunContext,
        stage_input: InfrastructureStageInput, config: InfrastructureStageConfig,
    ) -> StageResult[InfrastructureStageOutput]:
        value = self.validate_input(stage_input)
        if not isinstance(config, InfrastructureStageConfig):
            raise TypeError("infrastructure requires InfrastructureStageConfig")
        if not isinstance(value.demand_profile, DemographicDemandProfile):
            raise TypeError("invalid demographic demand profile")
        if not isinstance(value.network, NetworkBackend):
            raise TypeError("infrastructure requires canonical NetworkBackend")
        if (
            not isinstance(value.blocks, BlockZoneAssociationResult)
            or not isinstance(value.parcels, tuple)
            or not isinstance(value.buildings, tuple)
            or not isinstance(value.fixed_facilities, tuple)
            or not isinstance(value.fixed_building_footprints, tuple)
        ):
            raise TypeError("infrastructure inputs must use canonical immutable spatial types")
        srid = context.working_srid
        if (
            snapshot.snapshot_id != context.run_id
            or snapshot.settings.working_srid != srid
            or value.network.snapshot.working_crs.srid != srid
            or value.blocks.working_crs.srid != srid
        ):
            raise ValueError("infrastructure run or metric CRS mismatch")
        if context.mode is RunMode.FROM_SCRATCH and (
            snapshot.facilities or value.fixed_facilities or value.fixed_building_footprints
        ):
            raise ValueError("FROM_SCRATCH cannot consume fixed urban facilities or buildings")
        if snapshot.facilities and not value.fixed_facilities:
            raise ValueError("fixed facility refs require hydrated source records")
        if snapshot.buildings and not value.fixed_building_footprints:
            raise ValueError("fixed building refs require hydrated source footprints")
        if value.fixed_building_footprints and not snapshot.buildings:
            raise ValueError("resolved fixed building footprints require snapshot refs")
        count = sum(map(len, (
            value.blocks.blocks, value.parcels, value.buildings,
            value.fixed_facilities, value.fixed_building_footprints,
        )))
        if count > config.max_spatial_inputs:
            raise ValueError("infrastructure spatial input limit exceeded")
        if any(
            not isinstance(geom, BaseGeometry) or geom.is_empty or not geom.is_valid
            for geom in value.fixed_building_footprints
        ):
            raise ValueError("invalid fixed building footprint")
        types = tuple(sorted(config.infrastructure_types, key=lambda item: item.code))
        existing = ExistingInfrastructureAdapter().adapt(
            snapshot, records=value.fixed_facilities, infrastructure_types=types,
            mapping_rules=config.mapping_rules, skip_unmapped=config.skip_unmapped,
        )
        calculator = UnmetDemandCalculator()
        gross = calculator.calculate(
            value.demand_profile, infrastructure_types=types, existing=existing,
        )
        blocks = {item.cleaned_block.block_id: item.cleaned_block.geometry
                  for item in value.blocks.blocks}
        if any(item.block_id not in blocks for item in gross.demands):
            raise ValueError("infrastructure demand references missing block geometry")
        if any(item.working_srid != srid for item in value.buildings):
            raise ValueError("infrastructure building CRS mismatch")
        host_geometries = {item.building_id: item.geometry for item in value.buildings}
        if len(host_geometries) != len(value.buildings):
            raise ValueError("duplicate infrastructure host building identity")
        static_occupied = STRtree([
            *(item.geometry for item in value.buildings),
            *value.fixed_building_footprints,
            *(item.geometry for item in value.fixed_facilities),
        ])
        accepted_geometries: list[BaseGeometry] = []
        served: list[InfrastructureServedDemand] = []
        partials: list[tuple[
            InfrastructureType, InfrastructureCandidateGeometryResult,
            InfrastructureNetworkSnapBatchResult, InfrastructureAccessibilityBatchResult,
            InfrastructureAccessibilityBatchResult,
        ]] = []
        for infra_type in types:
            geometry = InfrastructureCandidateGeometryBuilder().build(
                InfrastructureCandidateSiteGenerator(
                    working_srid=srid, max_candidates=config.candidate_policy.max_candidates,
                    max_inputs=config.max_spatial_inputs,
                ).generate(
                    infra_type, zoned_blocks=value.blocks, parcels=value.parcels,
                    buildings=value.buildings,
                ),
                infrastructure_type=infra_type,
            )
            demands = tuple(
                item for item in gross.demands if item.infrastructure_type_code == infra_type.code
            )
            facilities = tuple(
                item for item in existing.facilities
                if item.infrastructure_type_code == infra_type.code
            )
            snap_inputs: list[InfrastructureNetworkSnapInput] = [
                InfrastructureDemandSnapInput(
                    ref=InfrastructureDemandRef.from_demand(item),
                    point=_point(blocks[item.block_id]), working_srid=srid,
                ) for item in demands
            ]
            snap_inputs.extend(
                ExistingInfrastructureFacilitySnapInput(
                    ref=ExistingInfrastructureFacilityRef.from_facility(item),
                    point=_point(item.geometry), working_srid=srid,
                ) for item in facilities
            )
            snap_inputs.extend(
                InfrastructureCandidateSnapInput(
                    ref=InfrastructureCandidateRef.from_candidate(item),
                    point=_point(item.anchor), working_srid=srid,
                ) for item in geometry.candidates
            )
            snaps = snap_infrastructure_network_batch(
                value.network, tuple(snap_inputs), policy=config.snap_policy,
            )
            existing_access = compute_existing_facility_accessibility_batch(
                value.network, snaps, infrastructure_type=infra_type,
            )
            candidate_access = compute_candidate_site_accessibility_batch(
                value.network, snaps, infrastructure_type=infra_type,
                policy=config.candidate_policy,
            )
            gross_by_key = {item.key: item.gross_demand for item in demands}
            capacity = {item.facility_id: item.capacity for item in facilities}
            # Existing T07 provides one nearest reachable facility per demand.
            # Allocate its finite capacity in stable demand-key order, not once per block.
            for outcome in sorted(existing_access.reachable, key=lambda row: row.demand_ref.key):
                ref = outcome.facility_site_ref
                if not isinstance(ref, ExistingInfrastructureFacilityRef):
                    raise ValueError("invalid existing facility accessibility reference")
                amount = min(gross_by_key[outcome.demand_ref.key], capacity[ref.facility_id])
                capacity[ref.facility_id] -= amount
                if amount > 0:
                    served.append(InfrastructureServedDemand(
                        block_id=outcome.demand_ref.block_id,
                        infrastructure_type_code=infra_type.code, value=amount,
                    ))
            partials.append((infra_type, geometry, snaps, existing_access, candidate_access))

        demand = calculator.calculate(
            value.demand_profile, infrastructure_types=types, existing=existing,
            served=tuple(served),
        )
        results: list[InfrastructureTypeStageOutput] = []
        for infra_type, geometry, snaps, existing_access, candidate_access in partials:
            state = initialize_infrastructure_greedy_placement_state(
                tuple(item for item in demand.demands
                      if item.infrastructure_type_code == infra_type.code),
                tuple(InfrastructureCandidateRef.from_candidate(item)
                      for item in geometry.candidates),
                candidate_accessibility=candidate_access,
            )
            by_key = {
                InfrastructureCandidateRef.from_candidate(item).key: item
                for item in geometry.candidates
            }
            for iteration in range(config.placement_policy.max_iterations):
                benefits = calculate_infrastructure_candidate_benefits(
                    state, infrastructure_type=infra_type,
                )
                dynamic_occupied = STRtree(accepted_geometries)
                feasibility = tuple(
                    _feasibility(
                        by_key[item.candidate_ref.key], infra_type=infra_type,
                        capacity=item.capacity, host_geometries=host_geometries,
                        static_occupied=static_occupied, dynamic_occupied=dynamic_occupied,
                    ) for item in benefits
                )
                selection = select_infrastructure_greedy_candidate(
                    state, benefits, iteration_index=iteration,
                    feasibility_results=feasibility, policy=config.placement_policy,
                )
                if selection.status is not InfrastructurePlacementSelectionStatus.SELECTED:
                    break
                if selection.selected is None:
                    raise ValueError("selected infrastructure has no candidate")
                candidate = by_key[selection.selected.candidate_ref.key]
                occupied = _occupied_geometry(candidate, host_geometries)
                if occupied is None:
                    raise ValueError("accepted infrastructure lacks geometry")
                accepted_geometries.append(occupied)
                state = apply_infrastructure_greedy_selection(
                    state, selection, infrastructure_type=infra_type,
                )
            results.append(InfrastructureTypeStageOutput(
                infra_type, geometry, snaps, existing_access, candidate_access, state,
            ))
        output = InfrastructureStageOutput(
            existing=existing, demand=demand, types=tuple(results),
            metrics=InfrastructureMetricsBuilder().build(
                demand, infrastructure_types=types,
                placements=tuple(item.placement for item in results),
                existing_accessibility=tuple(item.existing_accessibility for item in results),
            ),
        )
        fingerprint = StageFingerprintWriter()
        for part in (
            self.name, self.version, str(snapshot.snapshot_id), str(context.seed),
            repr(config), value.network.snapshot.snapshot_id,
        ):
            fingerprint.add_text(part)
        for facility in output.existing.facilities:
            fingerprint.add_text(facility.facility_id)
            fingerprint.add_text(repr(facility.capacity))
            fingerprint.add_bytes(facility.geometry.wkb)
        for demand_item in demand.demands:
            fingerprint.add_text(repr(demand_item))
        for type_result in results:
            for candidate in type_result.candidate_geometry.candidates:
                fingerprint.add_text(candidate.candidate_id)
                fingerprint.add_bytes(candidate.anchor.wkb)
                if candidate.site_geometry is not None:
                    fingerprint.add_bytes(candidate.site_geometry.wkb)
                fingerprint.add_text(candidate.host_building_id or "")
            for snapped in type_result.snaps.snapped:
                fingerprint.add_text(repr(snapped))
            for unsnapped in type_result.snaps.unsnapped:
                fingerprint.add_text(repr(unsnapped))
            for access in (type_result.existing_accessibility, type_result.candidate_accessibility):
                for reachable in access.reachable:
                    fingerprint.add_text(repr(reachable))
                for unavailable in access.unavailable:
                    fingerprint.add_text(repr(unavailable))
            for accepted in type_result.placement.accepted_facilities:
                fingerprint.add_text(repr(accepted))
            for remaining in type_result.placement.remaining_demand:
                fingerprint.add_text(repr(remaining))
        for metric in output.metrics.raw_metrics:
            fingerprint.add_text(repr(metric))
        return StageResult(
            output=output, fingerprint=fingerprint.finalize(),
            diagnostics=(StageDiagnostic(
                code="infrastructure.completed",
                message=f"Placed {len(accepted_geometries)} infrastructure facilities",
            ),),
        )


def _occupied_geometry(
    candidate: InfrastructureCandidateGeometry, hosts: dict[str, BaseGeometry],
) -> BaseGeometry | None:
    if candidate.site_geometry is not None:
        return candidate.site_geometry
    return hosts.get(candidate.host_building_id or "")


def _feasibility(
    candidate: InfrastructureCandidateGeometry, *, infra_type: InfrastructureType,
    capacity: float, host_geometries: dict[str, BaseGeometry],
    static_occupied: STRtree, dynamic_occupied: STRtree,
) -> InfrastructureFeasibilityResult:
    host = host_geometries.get(candidate.host_building_id or "")
    result = validate_infrastructure_candidate_feasibility(
        candidate, infrastructure_type=infra_type, proposed_capacity=capacity,
        host_building_geometry=host,
        host_building_working_srid=candidate.working_srid if host is not None else None,
    )
    geometry = _occupied_geometry(candidate, host_geometries)
    if geometry is None:
        return result
    occupied = len(dynamic_occupied.query(geometry, predicate="intersects")) > 0
    if candidate.site_geometry is not None:
        occupied = occupied or len(static_occupied.query(geometry, predicate="intersects")) > 0
    if occupied:
        return replace(
            result, is_feasible=False,
            rejection_reasons=(
                *result.rejection_reasons,
                InfrastructureFeasibilityRejectionReason.OCCUPIED_LOCATION,
            ),
        )
    return result
