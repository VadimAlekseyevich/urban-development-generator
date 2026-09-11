from enum import StrEnum


class PipelineStage(StrEnum):
    ANALYZE_TERRITORY = "analyze_territory"
    ZONING = "zoning"
    ROADS = "roads"
    BLOCKS = "blocks"
    BUILDINGS = "buildings"
    POPULATION = "population"
    INFRASTRUCTURE = "infrastructure"
    VALIDATION = "validation"
    METRICS = "metrics"
