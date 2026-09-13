from core.urban_generator.suitability.aggregation import (
    WeightedSuitabilityError,
    WeightedSuitabilityResult,
    aggregate_weighted_suitability,
)
from core.urban_generator.suitability.config import (
    SuitabilityConfig,
    SuitabilityConfigError,
    SuitabilityFactorConfig,
    SuitabilityNormalization,
    SuitabilityThresholds,
)
from core.urban_generator.suitability.dem_slope import (
    DEMSlopeError,
    DEMSlopeFactor,
    DEMSlopeNoDataPolicy,
    DEMSlopeSource,
    DEMSlopeWindow,
)
from core.urban_generator.suitability.factors import (
    SuitabilityFactor,
    SuitabilityFactorError,
    SuitabilityFactorResult,
    SuitabilityGridSpec,
    validate_factor_result,
)
from core.urban_generator.suitability.hard_exclusion import (
    ExclusionRasterizationPolicy,
    HardExclusionBoundary,
    HardExclusionGeometryLayer,
    HardExclusionMask,
    HardExclusionMaskError,
    HardExclusionRasterLayer,
    build_hard_exclusion_mask,
)
from core.urban_generator.suitability.landuse import (
    LanduseClassWeight,
    LanduseClassWeights,
    LanduseFactor,
    LanduseFactorError,
    LanduseSource,
    LanduseUnknownClassPolicy,
    LanduseWindow,
)
from core.urban_generator.suitability.road_proximity import (
    RoadProximityError,
    RoadProximityFactor,
    RoadProximityIndex,
    RoadProximityTile,
)

__all__ = [
    "DEMSlopeError",
    "DEMSlopeFactor",
    "DEMSlopeNoDataPolicy",
    "DEMSlopeSource",
    "DEMSlopeWindow",
    "ExclusionRasterizationPolicy",
    "HardExclusionBoundary",
    "HardExclusionGeometryLayer",
    "HardExclusionMask",
    "HardExclusionMaskError",
    "HardExclusionRasterLayer",
    "LanduseClassWeight",
    "LanduseClassWeights",
    "LanduseFactor",
    "LanduseFactorError",
    "LanduseSource",
    "LanduseUnknownClassPolicy",
    "LanduseWindow",
    "RoadProximityError",
    "RoadProximityFactor",
    "RoadProximityIndex",
    "RoadProximityTile",
    "SuitabilityConfig",
    "SuitabilityConfigError",
    "SuitabilityFactor",
    "SuitabilityFactorConfig",
    "SuitabilityFactorError",
    "SuitabilityFactorResult",
    "SuitabilityGridSpec",
    "SuitabilityNormalization",
    "SuitabilityThresholds",
    "WeightedSuitabilityError",
    "WeightedSuitabilityResult",
    "aggregate_weighted_suitability",
    "build_hard_exclusion_mask",
    "validate_factor_result",
]
