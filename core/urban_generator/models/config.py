from dataclasses import dataclass, field


@dataclass(slots=True, frozen=True)
class SuitabilityWeights:
    slope: float = 1.0
    protected_areas: float = 1.0
    water: float = 1.0
    existing_access: float = 0.6


@dataclass(slots=True, frozen=True)
class GenerationConfig:
    seed: int
    target_population: int | None = None
    target_density_per_km2: float | None = None
    max_slope_degrees: float = 15.0
    road_spacing_m: float = 180.0
    block_target_area_m2: float = 20_000.0
    building_setback_m: float = 5.0
    min_building_gap_m: float = 8.0
    infrastructure_categories: tuple[str, ...] = (
        "education",
        "healthcare",
        "retail",
        "recreation",
    )
    suitability_weights: SuitabilityWeights = field(default_factory=SuitabilityWeights)
