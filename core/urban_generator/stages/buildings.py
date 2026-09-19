from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass

from shapely.affinity import rotate
from shapely.geometry import LineString, Polygon
from shapely.geometry.base import BaseGeometry

from core.urban_generator.blocks import PlanningParcel
from core.urban_generator.buildings import (
    BarFootprintAxis,
    BarFootprintAxisSource,
    BarFootprintSpec,
    BarFrontageFootprintStrategy,
    BuildingArchetype,
    BuildingArchetypeConfig,
    BuildingAreaBaseline,
    BuildingAreaCalculationResult,
    BuildingAreaMetricsCalculator,
    BuildingAreaSubject,
    BuildingAttributeAssigner,
    BuildingAttributeAssignmentResult,
    BuildingAttributeConfig,
    BuildingAttributeSubject,
    BuildingConfig,
    BuildingDevelopableMask,
    BuildingEnvelopePolicy,
    BuildingEnvelopeResult,
    BuildingEnvelopeSource,
    BuildingEnvelopeSourceKind,
    BuildingEnvelopeStatus,
    BuildingFootprintStatus,
    BuildingFootprintStrategy,
    BuildingOrientationAxis,
    BuildingOrientationPolicy,
    BuildingOrientationResult,
    BuildingOrientationSource,
    BuildingOrientationStrategy,
    BuildingPlacementBaseline,
    BuildingPlacementCandidate,
    BuildingPlacementCandidateGenerator,
    BuildingPlacementCandidatePolicy,
    BuildingPlacementConvergenceResult,
    BuildingPlacementConverger,
    BuildingPlacementFrontage,
    BuildingPlacementProposal,
    BuildingPlacementScope,
    BuildingPlacementTargets,
    BuildingSpacingPolicy,
    EngineBuildingEnvelopeConstraintEvaluator,
    PerimeterCourtyardFootprintSpec,
    PerimeterCourtyardFootprintStrategy,
    PlacedBuildingFootprint,
    RectangularPointFootprintSpec,
    RectangularPointFootprintStrategy,
)
from core.urban_generator.domain import (
    RunContext,
    RunMode,
    SnapshotLayerRef,
    StageDiagnostic,
    StageDiagnosticLevel,
    StageFingerprint,
    StageResult,
    TerritorySnapshot,
    build_stage_fingerprint,
    require_stage_input,
)
from core.urban_generator.stages.blocks import BlocksAndParcelsStageOutput
from core.urban_generator.stages.catalog import (
    BLOCKS_AND_PARCELS_STAGE,
    BUILDINGS_STAGE,
)
from core.urban_generator.zoning import ZoneClass


@dataclass(frozen=True, slots=True)
class BuildingZoneTarget:
    zone_class: ZoneClass
    target_coverage_ratio: float
    target_far: float
    coverage_tolerance: float = 0.02
    far_tolerance: float = 0.10

    def __post_init__(self) -> None:
        if not isinstance(self.zone_class, ZoneClass):
            raise TypeError("zone_class must be ZoneClass")
        for name in (
            "target_coverage_ratio",
            "target_far",
            "coverage_tolerance",
            "far_tolerance",
        ):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or float(value) < 0.0
            ):
                raise ValueError(f"{name} must be finite and non-negative")
        if self.target_coverage_ratio > 1.0:
            raise ValueError("target_coverage_ratio must be <= 1")
        if self.coverage_tolerance > 1.0:
            raise ValueError("coverage_tolerance must be <= 1")

    def targets(self, *, site_area_m2: float) -> BuildingPlacementTargets:
        return BuildingPlacementTargets(
            site_area_m2=site_area_m2,
            target_coverage_ratio=float(self.target_coverage_ratio),
            target_far=float(self.target_far),
            coverage_tolerance=float(self.coverage_tolerance),
            far_tolerance=float(self.far_tolerance),
        )


FootprintSpec = (
    RectangularPointFootprintSpec
    | BarFootprintSpec
    | PerimeterCourtyardFootprintSpec
)


@dataclass(frozen=True, slots=True)
class BuildingArchetypeStageSpec:
    archetype: BuildingArchetype
    footprint_spec: FootprintSpec
    planning_floor_area_multiplier: float = 1.0
    priority: float = 0.0

    def __post_init__(self) -> None:
        if not isinstance(self.archetype, BuildingArchetype):
            raise TypeError("archetype must be BuildingArchetype")
        if not isinstance(
            self.footprint_spec,
            (
                RectangularPointFootprintSpec,
                BarFootprintSpec,
                PerimeterCourtyardFootprintSpec,
            ),
        ):
            raise TypeError("unsupported building footprint spec")
        multiplier = self.planning_floor_area_multiplier
        if (
            isinstance(multiplier, bool)
            or not isinstance(multiplier, (int, float))
            or not math.isfinite(float(multiplier))
            or float(multiplier) <= 0.0
        ):
            raise ValueError(
                "planning_floor_area_multiplier must be positive and finite"
            )
        priority = self.priority
        if (
            isinstance(priority, bool)
            or not isinstance(priority, (int, float))
            or not math.isfinite(float(priority))
        ):
            raise ValueError("priority must be finite")


@dataclass(frozen=True, slots=True)
class BuildingSourceBaseline:
    source_id: str
    placement: BuildingPlacementBaseline = BuildingPlacementBaseline()
    area: BuildingAreaBaseline = BuildingAreaBaseline()

    def __post_init__(self) -> None:
        if not isinstance(self.source_id, str) or not self.source_id.strip():
            raise ValueError("source_id must be a non-empty string")
        if not isinstance(self.placement, BuildingPlacementBaseline):
            raise TypeError("placement must be BuildingPlacementBaseline")
        if not isinstance(self.area, BuildingAreaBaseline):
            raise TypeError("area must be BuildingAreaBaseline")


@dataclass(frozen=True, slots=True)
class BuildingStageInput:
    blocks: BlocksAndParcelsStageOutput
    existing_footprints: tuple[PlacedBuildingFootprint, ...] = ()
    source_baselines: tuple[BuildingSourceBaseline, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.blocks, BlocksAndParcelsStageOutput):
            raise TypeError("blocks must be BlocksAndParcelsStageOutput")
        if not isinstance(self.existing_footprints, tuple):
            raise TypeError("existing_footprints must be an immutable tuple")
        if any(
            not isinstance(item, PlacedBuildingFootprint)
            for item in self.existing_footprints
        ):
            raise TypeError(
                "existing_footprints must contain PlacedBuildingFootprint values"
            )
        if not isinstance(self.source_baselines, tuple):
            raise TypeError("source_baselines must be an immutable tuple")
        if any(
            not isinstance(item, BuildingSourceBaseline)
            for item in self.source_baselines
        ):
            raise TypeError(
                "source_baselines must contain BuildingSourceBaseline values"
            )
        source_ids = tuple(item.source_id for item in self.source_baselines)
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("source baseline ids must be unique")


@dataclass(frozen=True, slots=True)
class BuildingStageConfig:
    building: BuildingConfig
    attributes: BuildingAttributeConfig
    archetype_specs: tuple[BuildingArchetypeStageSpec, ...]
    zone_targets: tuple[BuildingZoneTarget, ...]
    envelope_policy: BuildingEnvelopePolicy = BuildingEnvelopePolicy()
    candidate_policy: BuildingPlacementCandidatePolicy = (
        BuildingPlacementCandidatePolicy()
    )
    orientation_policy: BuildingOrientationPolicy = BuildingOrientationPolicy()
    spacing_policy: BuildingSpacingPolicy = BuildingSpacingPolicy()
    max_sources: int = 100_000
    max_grid_scan_cells: int = 1_000_000
    max_frontage_samples: int = 1_000_000
    max_candidates_per_source: int = 50_000
    max_iterations_per_source: int = 50_000
    max_spacing_footprints: int = 100_000
    max_spacing_candidates: int = 20_000

    def __post_init__(self) -> None:
        if not isinstance(self.building, BuildingConfig):
            raise TypeError("building must be BuildingConfig")
        if not isinstance(self.attributes, BuildingAttributeConfig):
            raise TypeError("attributes must be BuildingAttributeConfig")
        if not isinstance(self.archetype_specs, tuple):
            raise TypeError("archetype_specs must be an immutable tuple")
        if any(
            not isinstance(item, BuildingArchetypeStageSpec)
            for item in self.archetype_specs
        ):
            raise TypeError(
                "archetype_specs must contain BuildingArchetypeStageSpec values"
            )
        archetypes = tuple(item.archetype for item in self.archetype_specs)
        if len(archetypes) != len(set(archetypes)):
            raise ValueError("archetype_specs must be unique by archetype")
        if set(archetypes) != {
            profile.archetype for profile in self.building.archetypes
        }:
            raise ValueError(
                "archetype_specs must cover exactly the configured archetypes"
            )
        if not isinstance(self.zone_targets, tuple):
            raise TypeError("zone_targets must be an immutable tuple")
        if any(
            not isinstance(item, BuildingZoneTarget)
            for item in self.zone_targets
        ):
            raise TypeError(
                "zone_targets must contain BuildingZoneTarget values"
            )
        zones = tuple(item.zone_class for item in self.zone_targets)
        if len(zones) != len(set(zones)):
            raise ValueError("zone_targets must be unique by zone_class")
        for name in (
            "max_sources",
            "max_grid_scan_cells",
            "max_frontage_samples",
            "max_candidates_per_source",
            "max_iterations_per_source",
            "max_spacing_footprints",
            "max_spacing_candidates",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")


@dataclass(frozen=True, slots=True)
class BuildingSourceStageResult:
    source_id: str
    source_kind: BuildingEnvelopeSourceKind
    zone_class: ZoneClass
    archetype: BuildingArchetype | None
    envelope: BuildingEnvelopeResult
    candidate_count: int
    proposal_count: int
    placement: BuildingPlacementConvergenceResult | None


@dataclass(frozen=True, slots=True)
class BuildingStageOutput:
    sources: tuple[BuildingSourceStageResult, ...]
    accepted_proposals: tuple[BuildingPlacementProposal, ...]
    attributes: BuildingAttributeAssignmentResult
    area_subjects: tuple[BuildingAreaSubject, ...]
    area_metrics: BuildingAreaCalculationResult
    fixed_building_refs: tuple[SnapshotLayerRef, ...]


@dataclass(frozen=True, slots=True)
class _ResolvedBuildingSource:
    source: BuildingEnvelopeSource
    mask: BuildingDevelopableMask
    zone_class: ZoneClass
    frontages: tuple[BuildingPlacementFrontage, ...]

    @property
    def site_area_m2(self) -> float:
        return float(self.source.geometry.area)


class BuildingStage:
    """Compose S08 capabilities from S07 block/parcel output."""

    name = BUILDINGS_STAGE
    version = "1.0.0"
    dependencies = (BLOCKS_AND_PARCELS_STAGE,)

    def __init__(
        self,
        *,
        constraint_evaluators: tuple[
            EngineBuildingEnvelopeConstraintEvaluator, ...
        ] = (),
    ) -> None:
        if not isinstance(constraint_evaluators, tuple):
            raise TypeError(
                "constraint_evaluators must be an immutable tuple"
            )
        if any(
            not isinstance(item, EngineBuildingEnvelopeConstraintEvaluator)
            for item in constraint_evaluators
        ):
            raise TypeError(
                "constraint_evaluators must contain "
                "EngineBuildingEnvelopeConstraintEvaluator values"
            )
        self._constraint_evaluators = tuple(
            sorted(
                constraint_evaluators,
                key=lambda item: item.evaluation_id,
            )
        )

    def validate_input(self, value: object) -> BuildingStageInput:
        return require_stage_input(
            value,
            BuildingStageInput,
            stage_name=self.name,
        )

    def execute(
        self,
        *,
        snapshot: TerritorySnapshot,
        context: RunContext,
        stage_input: BuildingStageInput,
        config: BuildingStageConfig,
    ) -> StageResult[BuildingStageOutput]:
        value = self.validate_input(stage_input)
        if not isinstance(config, BuildingStageConfig):
            raise TypeError("buildings config must be BuildingStageConfig")
        self._validate_alignment(
            snapshot=snapshot,
            context=context,
            value=value,
            config=config,
        )

        sources = _resolve_sources(
            value.blocks,
            building_config=config.building,
            working_srid=context.working_srid,
        )
        if len(sources) > config.max_sources:
            raise ValueError(
                f"building source limit exceeded: "
                f"{len(sources)} > {config.max_sources}"
            )

        spec_by_archetype = {
            item.archetype: item for item in config.archetype_specs
        }
        target_by_zone = {
            item.zone_class: item for item in config.zone_targets
        }
        baseline_by_source = {
            item.source_id: item for item in value.source_baselines
        }
        source_ids = {item.source.source_id for item in sources}
        unknown_baselines = set(baseline_by_source) - source_ids
        if unknown_baselines:
            raise ValueError(
                "building source baselines reference unknown sources: "
                + ", ".join(sorted(unknown_baselines))
            )
        envelope_builder = BuildableEnvelopeBuilder(
            working_srid=context.working_srid,
            policy=config.envelope_policy,
        )
        candidate_generator = BuildingPlacementCandidateGenerator(
            working_srid=context.working_srid,
            policy=config.candidate_policy,
            max_grid_scan_cells=config.max_grid_scan_cells,
            max_frontage_samples=config.max_frontage_samples,
            max_candidates=config.max_candidates_per_source,
        )
        orientation_strategy = BuildingOrientationStrategy(
            working_srid=context.working_srid,
            policy=config.orientation_policy,
        )

        source_results: list[BuildingSourceStageResult] = []
        accepted: list[BuildingPlacementProposal] = []
        accepted_meta: dict[str, tuple[ZoneClass, BuildingArchetype]] = {}
        spacing_footprints = list(value.existing_footprints)

        for resolved in sources:
            target_policy = target_by_zone.get(resolved.zone_class)
            if target_policy is None:
                raise ValueError(
                    "missing building zone target for "
                    f"{resolved.zone_class.value!r}"
                )
            envelope = envelope_builder.build(
                resolved.source,
                developable_mask=resolved.mask,
                snapshot=snapshot,
                context=context,
                constraint_evaluators=self._constraint_evaluators,
            )
            if envelope.status is not BuildingEnvelopeStatus.READY:
                source_results.append(
                    BuildingSourceStageResult(
                        source_id=resolved.source.source_id,
                        source_kind=resolved.source.source_kind,
                        zone_class=resolved.zone_class,
                        archetype=None,
                        envelope=envelope,
                        candidate_count=0,
                        proposal_count=0,
                        placement=None,
                    )
                )
                continue

            profile = _select_archetype(
                source=resolved,
                config=config.building,
                context=context,
            )
            stage_spec = spec_by_archetype[profile.archetype]
            _validate_strategy_spec(
                strategy=profile.footprint_strategy,
                stage_spec=stage_spec,
                source_kind=resolved.source.source_kind,
            )
            proposals, candidate_count = _proposals_for_source(
                resolved=resolved,
                envelope=envelope,
                profile_strategy=profile.footprint_strategy,
                stage_spec=stage_spec,
                candidate_generator=candidate_generator,
                orientation_strategy=orientation_strategy,
                working_srid=context.working_srid,
            )

            baseline = baseline_by_source.get(
                resolved.source.source_id,
                BuildingSourceBaseline(resolved.source.source_id),
            )
            placement = BuildingPlacementConverger(
                working_srid=context.working_srid,
                spacing_policy=config.spacing_policy,
                max_proposals=max(1, config.max_candidates_per_source),
                max_iterations=config.max_iterations_per_source,
                max_spacing_footprints=config.max_spacing_footprints,
                max_spacing_candidates=config.max_spacing_candidates,
            ).converge(
                proposals,
                targets=target_policy.targets(
                    site_area_m2=resolved.site_area_m2
                ),
                baseline=baseline.placement,
                existing_footprints=tuple(spacing_footprints),
            )
            source_results.append(
                BuildingSourceStageResult(
                    source_id=resolved.source.source_id,
                    source_kind=resolved.source.source_kind,
                    zone_class=resolved.zone_class,
                    archetype=profile.archetype,
                    envelope=envelope,
                    candidate_count=candidate_count,
                    proposal_count=len(proposals),
                    placement=placement,
                )
            )
            for proposal in placement.accepted:
                accepted.append(proposal)
                accepted_meta[proposal.proposal_id] = (
                    resolved.zone_class,
                    profile.archetype,
                )
                spacing_footprints.append(
                    PlacedBuildingFootprint(
                        building_id=proposal.proposal_id,
                        geometry=proposal.geometry,
                        working_srid=context.working_srid,
                    )
                )

        ordered_accepted = tuple(
            sorted(accepted, key=lambda item: item.proposal_id)
        )
        attribute_subjects = tuple(
            BuildingAttributeSubject(
                building_id=proposal.proposal_id,
                source_id=proposal.source_id,
                zone_class=accepted_meta[proposal.proposal_id][0],
                archetype=accepted_meta[proposal.proposal_id][1],
            )
            for proposal in ordered_accepted
        )
        attributes = BuildingAttributeAssigner(
            max_subjects=max(1, config.max_sources * config.max_candidates_per_source)
        ).assign(
            attribute_subjects,
            config=config.attributes,
            context=context,
        )
        attributes_by_id = {
            item.building_id: item for item in attributes.buildings
        }
        area_subjects = tuple(
            BuildingAreaSubject(
                building_id=proposal.proposal_id,
                geometry=proposal.geometry,
                attributes=attributes_by_id[proposal.proposal_id],
                working_srid=context.working_srid,
            )
            for proposal in ordered_accepted
        )
        total_site_area = math.fsum(source.site_area_m2 for source in sources)
        if total_site_area <= 0.0:
            raise ValueError("building stage requires positive total source area")
        total_area_baseline = BuildingAreaBaseline(
            footprint_area_m2=math.fsum(
                item.area.footprint_area_m2
                for item in value.source_baselines
            ),
            gfa_m2=math.fsum(
                item.area.gfa_m2 for item in value.source_baselines
            ),
        )
        area_metrics = BuildingAreaMetricsCalculator(
            working_srid=context.working_srid,
            max_subjects=max(1, config.max_sources * config.max_candidates_per_source),
        ).calculate(
            area_subjects,
            site_area_m2=total_site_area,
            baseline=total_area_baseline,
        )
        output = BuildingStageOutput(
            sources=tuple(source_results),
            accepted_proposals=ordered_accepted,
            attributes=attributes,
            area_subjects=area_subjects,
            area_metrics=area_metrics,
            fixed_building_refs=snapshot.buildings,
        )
        unmet = sum(
            result.placement is not None
            and not result.placement.diagnostics.targets_met
            for result in source_results
        )
        blocked = sum(
            result.envelope.status is not BuildingEnvelopeStatus.READY
            for result in source_results
        )
        return StageResult(
            output=output,
            fingerprint=_fingerprint(
                snapshot=snapshot,
                context=context,
                config=config,
                output=output,
                evaluator_ids=tuple(
                    item.evaluation_id for item in self._constraint_evaluators
                ),
            ),
            diagnostics=(
                StageDiagnostic(
                    code="buildings.completed",
                    message=(
                        f"buildings completed: {len(sources)} sources, "
                        f"{len(ordered_accepted)} accepted buildings, "
                        f"{unmet} unmet targets, {blocked} blocked envelopes"
                    ),
                    level=(
                        StageDiagnosticLevel.WARNING
                        if unmet or blocked
                        else StageDiagnosticLevel.INFO
                    ),
                ),
            ),
        )

    @staticmethod
    def _validate_alignment(
        *,
        snapshot: TerritorySnapshot,
        context: RunContext,
        value: BuildingStageInput,
        config: BuildingStageConfig,
    ) -> None:
        working_srid = context.working_srid
        if snapshot.settings.working_srid != working_srid:
            raise ValueError("building working_srid must match snapshot")
        if value.blocks.subdivision.working_crs.srid != working_srid:
            raise ValueError(
                "building block/parcel working_srid must match run context"
            )
        if any(
            item.working_srid != working_srid
            for item in value.existing_footprints
        ):
            raise ValueError(
                "existing building footprints must match run working_srid"
            )
        if context.mode is RunMode.FROM_SCRATCH:
            if snapshot.buildings:
                raise ValueError(
                    "FROM_SCRATCH building stage must not consume fixed building refs"
                )
            if value.existing_footprints or value.source_baselines:
                raise ValueError(
                    "FROM_SCRATCH building stage must not consume fixed building state"
                )
        elif snapshot.buildings and not value.existing_footprints:
            raise ValueError(
                "EXPANSION snapshot building refs require resolved existing footprints"
            )
        for profile in config.building.archetypes:
            for zone_class in profile.allowed_zones:
                config.attributes.rule(zone_class, profile.archetype)


def _resolve_sources(
    blocks: BlocksAndParcelsStageOutput,
    *,
    building_config: BuildingConfig,
    working_srid: int,
) -> tuple[_ResolvedBuildingSource, ...]:
    parcels_by_block: dict[str, list[PlanningParcel]] = {}
    for parcel in blocks.subdivision.parcels:
        parcels_by_block.setdefault(parcel.block_id, []).append(parcel)

    sources: list[_ResolvedBuildingSource] = []
    for item in sorted(
        blocks.association.blocks,
        key=lambda value: value.cleaned_block.block_id,
    ):
        block_id = item.cleaned_block.block_id
        zone_class = item.association.zone_class
        if zone_class is None:
            continue
        profiles = building_config.eligible_archetypes(zone_class)
        parcel_allowed = any(
            _scope_allows(
                profile.placement_scope,
                BuildingEnvelopeSourceKind.PARCEL,
            )
            for profile in profiles
        )
        block_allowed = any(
            _scope_allows(
                profile.placement_scope,
                BuildingEnvelopeSourceKind.BLOCK,
            )
            for profile in profiles
        )
        block_parcels = tuple(
            sorted(
                parcels_by_block.get(block_id, ()),
                key=lambda parcel: parcel.parcel_id,
            )
        )
        if block_parcels and parcel_allowed:
            for parcel in block_parcels:
                if parcel.zone_class is None:
                    continue
                sources.append(
                    _ResolvedBuildingSource(
                        source=BuildingEnvelopeSource(
                            source_id=parcel.parcel_id,
                            source_kind=BuildingEnvelopeSourceKind.PARCEL,
                            geometry=parcel.geometry,
                            working_srid=working_srid,
                        ),
                        mask=BuildingDevelopableMask(
                            geometry=parcel.buildable_envelope,
                            working_srid=working_srid,
                        ),
                        zone_class=parcel.zone_class,
                        frontages=tuple(
                            BuildingPlacementFrontage(
                                road_id=frontage.road_id,
                                geometry=frontage.geometry,
                            )
                            for frontage in parcel.frontages
                        ),
                    )
                )
            continue
        if block_allowed:
            sources.append(
                _ResolvedBuildingSource(
                    source=BuildingEnvelopeSource(
                        source_id=block_id,
                        source_kind=BuildingEnvelopeSourceKind.BLOCK,
                        geometry=item.cleaned_block.geometry,
                        working_srid=working_srid,
                    ),
                    mask=BuildingDevelopableMask(
                        geometry=item.cleaned_block.geometry,
                        working_srid=working_srid,
                    ),
                    zone_class=zone_class,
                    frontages=(),
                )
            )
    return tuple(
        sorted(sources, key=lambda item: item.source.source_id)
    )


def _select_archetype(
    *,
    source: _ResolvedBuildingSource,
    config: BuildingConfig,
    context: RunContext,
) -> BuildingArchetypeConfig:
    profiles = tuple(
        profile
        for profile in config.eligible_archetypes(source.zone_class)
        if _scope_allows(profile.placement_scope, source.source.source_kind)
    )
    if not profiles:
        raise ValueError(
            "no eligible building archetype for "
            f"{source.zone_class.value}/{source.source.source_kind.value}"
        )
    total_weight = math.fsum(profile.selection_weight for profile in profiles)
    rng = context.rng(
        "buildings.archetype:"
        f"{config.fingerprint}:{source.source.source_id}"
    )
    threshold = float(rng.random()) * total_weight
    cumulative = 0.0
    for profile in profiles:
        cumulative += profile.selection_weight
        if threshold < cumulative:
            return profile
    return profiles[-1]


def _scope_allows(
    scope: BuildingPlacementScope,
    source_kind: BuildingEnvelopeSourceKind,
) -> bool:
    if scope is BuildingPlacementScope.PARCEL_OR_BLOCK:
        return True
    if source_kind is BuildingEnvelopeSourceKind.PARCEL:
        return scope is BuildingPlacementScope.PARCEL
    return scope is BuildingPlacementScope.BLOCK


def _validate_strategy_spec(
    *,
    strategy: BuildingFootprintStrategy,
    stage_spec: BuildingArchetypeStageSpec,
    source_kind: BuildingEnvelopeSourceKind,
) -> None:
    spec = stage_spec.footprint_spec
    if strategy is BuildingFootprintStrategy.RECTANGULAR_POINT:
        if not isinstance(spec, RectangularPointFootprintSpec):
            raise ValueError(
                f"{stage_spec.archetype.value} requires rectangular/point spec"
            )
        return
    if strategy is BuildingFootprintStrategy.BAR:
        if not isinstance(spec, BarFootprintSpec):
            raise ValueError(f"{stage_spec.archetype.value} requires bar spec")
        return
    if not isinstance(spec, PerimeterCourtyardFootprintSpec):
        raise ValueError(
            f"{stage_spec.archetype.value} requires perimeter/courtyard spec"
        )
    if source_kind is not BuildingEnvelopeSourceKind.BLOCK:
        raise ValueError(
            "perimeter/courtyard archetypes require block placement scope"
        )
    if (
        strategy is BuildingFootprintStrategy.PERIMETER
        and spec.kind.value != "perimeter"
    ):
        raise ValueError("perimeter strategy requires perimeter footprint spec")
    if (
        strategy is BuildingFootprintStrategy.COURTYARD
        and spec.kind.value != "courtyard"
    ):
        raise ValueError("courtyard strategy requires courtyard footprint spec")


def _proposals_for_source(
    *,
    resolved: _ResolvedBuildingSource,
    envelope: BuildingEnvelopeResult,
    profile_strategy: BuildingFootprintStrategy,
    stage_spec: BuildingArchetypeStageSpec,
    candidate_generator: BuildingPlacementCandidateGenerator,
    orientation_strategy: BuildingOrientationStrategy,
    working_srid: int,
) -> tuple[tuple[BuildingPlacementProposal, ...], int]:
    spec = stage_spec.footprint_spec
    if profile_strategy in {
        BuildingFootprintStrategy.PERIMETER,
        BuildingFootprintStrategy.COURTYARD,
    }:
        assert isinstance(spec, PerimeterCourtyardFootprintSpec)
        result = PerimeterCourtyardFootprintStrategy(
            working_srid=working_srid
        ).create(
            envelope,
            spec=spec,
        )
        geometry = result.footprint_geometry
        if geometry is None:
            return (), 0
        return (
            (
                _proposal(
                    source_id=resolved.source.source_id,
                    archetype=stage_spec.archetype,
                    index=0,
                    geometry=geometry,
                    working_srid=working_srid,
                    stage_spec=stage_spec,
                ),
            ),
            0,
        )

    candidates = candidate_generator.generate(
        envelope,
        frontages=resolved.frontages,
    )
    frontage_axes = tuple(
        BuildingOrientationAxis(
            source=BuildingOrientationSource.FRONTAGE,
            source_ref=item.road_id,
            geometry=item.geometry,
            working_srid=working_srid,
        )
        for item in resolved.frontages
        if isinstance(item.geometry, LineString)
    )
    proposals: list[BuildingPlacementProposal] = []
    for index, candidate in enumerate(candidates.candidates):
        orientation = orientation_strategy.orient(
            candidate,
            envelope=envelope,
            frontage_axes=frontage_axes,
        )
        geometry = _candidate_footprint(
            candidate=candidate,
            envelope=envelope,
            orientation=orientation,
            strategy=profile_strategy,
            spec=spec,
            working_srid=working_srid,
        )
        if geometry is None:
            continue
        proposals.append(
            _proposal(
                source_id=resolved.source.source_id,
                archetype=stage_spec.archetype,
                index=index,
                geometry=geometry,
                working_srid=working_srid,
                stage_spec=stage_spec,
            )
        )
    return tuple(proposals), len(candidates.candidates)


def _candidate_footprint(
    *,
    candidate: BuildingPlacementCandidate,
    envelope: BuildingEnvelopeResult,
    orientation: BuildingOrientationResult,
    strategy: BuildingFootprintStrategy,
    spec: FootprintSpec,
    working_srid: int,
) -> BaseGeometry | None:
    if strategy is BuildingFootprintStrategy.RECTANGULAR_POINT:
        assert isinstance(spec, RectangularPointFootprintSpec)
        result = RectangularPointFootprintStrategy(
            working_srid=working_srid
        ).create(
            candidate,
            envelope=envelope,
            spec=spec,
        )
        if result.status is not BuildingFootprintStatus.READY:
            return None
        geometry: BaseGeometry = result.proposed_geometry
        if not math.isclose(
            orientation.angle_degrees,
            0.0,
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            geometry = rotate(
                geometry,
                orientation.angle_degrees,
                origin=(candidate.point.x, candidate.point.y),
            )
        buildable = envelope.buildable_geometry
        if (
            buildable is None
            or not isinstance(geometry, Polygon)
            or not buildable.covers(geometry)
        ):
            return None
        return geometry

    assert strategy is BuildingFootprintStrategy.BAR
    assert isinstance(spec, BarFootprintSpec)
    axis = _bar_axis(
        candidate=candidate,
        orientation=orientation,
        spec=spec,
        working_srid=working_srid,
    )
    result = BarFrontageFootprintStrategy(
        working_srid=working_srid
    ).create(
        candidate,
        envelope=envelope,
        axis=axis,
        spec=spec,
    )
    return result.footprint_geometry


def _bar_axis(
    *,
    candidate: BuildingPlacementCandidate,
    orientation: BuildingOrientationResult,
    spec: BarFootprintSpec,
    working_srid: int,
) -> BarFootprintAxis:
    if (
        orientation.source is BuildingOrientationSource.FRONTAGE
        and orientation.axis.distance(candidate.point) <= 1e-6
    ):
        geometry = orientation.axis
        source = BarFootprintAxisSource.FRONTAGE
    else:
        angle = math.radians(orientation.angle_degrees)
        half = max(spec.length_m, spec.depth_m) * 2.0
        dx = math.cos(angle) * half
        dy = math.sin(angle) * half
        geometry = LineString(
            (
                (candidate.point.x - dx, candidate.point.y - dy),
                (candidate.point.x + dx, candidate.point.y + dy),
            )
        )
        source = (
            BarFootprintAxisSource.ROAD
            if orientation.source is BuildingOrientationSource.ROAD
            else BarFootprintAxisSource.BLOCK
        )
    return BarFootprintAxis(
        source=source,
        source_ref=orientation.source_ref,
        geometry=geometry,
        working_srid=working_srid,
    )


def _proposal(
    *,
    source_id: str,
    archetype: BuildingArchetype,
    index: int,
    geometry: BaseGeometry,
    working_srid: int,
    stage_spec: BuildingArchetypeStageSpec,
) -> BuildingPlacementProposal:
    token = (
        f"{source_id}|{archetype.value}|{index}"
    ).encode("utf-8")
    digest = hashlib.blake2b(token, digest_size=10).hexdigest()
    return BuildingPlacementProposal(
        proposal_id=f"building:{digest}:{index:08d}",
        source_id=source_id,
        geometry=geometry,
        working_srid=working_srid,
        planning_floor_area_multiplier=(
            stage_spec.planning_floor_area_multiplier
        ),
        priority=stage_spec.priority,
    )


def _fingerprint(
    *,
    snapshot: TerritorySnapshot,
    context: RunContext,
    config: BuildingStageConfig,
    output: BuildingStageOutput,
    evaluator_ids: tuple[str, ...],
) -> StageFingerprint:
    parts: list[str | bytes] = [
        BuildingStage.name,
        BuildingStage.version,
        str(snapshot.snapshot_id),
        str(context.seed),
        config.building.fingerprint,
        config.attributes.fingerprint,
        repr(config.archetype_specs),
        repr(config.zone_targets),
        repr(config.envelope_policy),
        repr(config.candidate_policy),
        repr(config.orientation_policy),
        repr(config.spacing_policy),
        "|".join(evaluator_ids),
        "|".join(ref.source_ref for ref in snapshot.buildings),
    ]
    for source in output.sources:
        parts.extend(
            (
                source.source_id,
                source.source_kind.value,
                source.zone_class.value,
                source.archetype.value if source.archetype is not None else "",
                source.envelope.status.value,
                str(source.candidate_count),
                str(source.proposal_count),
            )
        )
    for subject in output.area_subjects:
        parts.extend(
            (
                subject.building_id,
                subject.attributes.archetype.value,
                subject.attributes.use.value,
                str(subject.attributes.floors),
                subject.geometry.wkb,
            )
        )
    return build_stage_fingerprint(*parts)
