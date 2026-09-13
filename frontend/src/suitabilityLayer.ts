import type { Bounds } from './sourceLayers'

export type SuitabilityImageCoordinate = [longitude: number, latitude: number]
export type SuitabilityImageCoordinates = [
  SuitabilityImageCoordinate,
  SuitabilityImageCoordinate,
  SuitabilityImageCoordinate,
  SuitabilityImageCoordinate,
]

export type SuitabilityFactorMetadata = {
  code: string
  version: string
  weight: number
  normalization: string
  raw_min: number | null
  raw_max: number | null
}

export type SuitabilityStatistics = {
  total_cells: number
  valid_cells: number
  hard_excluded_cells: number
  invalid_data_cells: number
  minimum_score_threshold: number
  preferred_score_threshold: number | null
  meets_minimum_cells: number
  preferred_cells: number | null
  min_score: number | null
  max_score: number | null
  mean_score: number | null
  p05_score: number | null
  p50_score: number | null
  p95_score: number | null
}

export type SuitabilityLayerMetadata = {
  artifact_id: string
  checksum: string
  size_bytes: number
  content_type: string | null
  schema_version: string
  config_version: string
  config_fingerprint: string
  working_srid: number
  working_bounds: [number, number, number, number]
  width: number
  height: number
  image_coordinates_wgs84: SuitabilityImageCoordinates
  wgs84_bounds: Bounds
  statistics: SuitabilityStatistics
  factors: SuitabilityFactorMetadata[]
  hard_exclusion_source_codes: string[]
}

export function formatScore(value: number | null): string {
  return value === null ? '—' : value.toFixed(3)
}

export function formatPercent(numerator: number, denominator: number): string {
  if (denominator <= 0) return '—'
  return `${((numerator / denominator) * 100).toFixed(1)}%`
}

export function factorNormalizationLabel(factor: SuitabilityFactorMetadata): string {
  if (factor.normalization === 'IDENTITY') return 'identity'
  if (factor.raw_min === null || factor.raw_max === null) return factor.normalization
  const direction = factor.normalization === 'INVERTED_MIN_MAX' ? 'inv' : 'min/max'
  return `${direction} ${factor.raw_min}…${factor.raw_max}`
}
