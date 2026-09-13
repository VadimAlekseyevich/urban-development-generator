export type SourceLayerApiName = 'roads' | 'buildings' | 'water' | 'landuse'
export type SourceLayerKey = 'boundary' | SourceLayerApiName

export type Position = [number, number, ...number[]]

export type GeoJsonGeometry =
  | { type: 'Point'; coordinates: Position }
  | { type: 'MultiPoint'; coordinates: Position[] }
  | { type: 'LineString'; coordinates: Position[] }
  | { type: 'MultiLineString'; coordinates: Position[][] }
  | { type: 'Polygon'; coordinates: Position[][] }
  | { type: 'MultiPolygon'; coordinates: Position[][][] }
  | { type: 'GeometryCollection'; geometries: GeoJsonGeometry[] }

export type GeoJsonFeature = {
  type: 'Feature'
  id: string
  geometry: GeoJsonGeometry
  properties: Record<string, unknown>
}

export type GeoJsonFeatureCollection = {
  type: 'FeatureCollection'
  features: GeoJsonFeature[]
}

export type SourceLayerResponse = GeoJsonFeatureCollection & {
  project_id: string
  dataset_version_id: string
  layer: SourceLayerApiName
  query_bbox: [number, number, number, number]
  geojson_crs: 'EPSG:4326'
  working_srid: number
  limit: number
  truncated: boolean
}

export type ProjectBoundaryResponse = {
  type: 'Feature'
  id: string
  geometry: GeoJsonGeometry | null
  properties: {
    project_id: string
    working_srid: number
    geojson_crs: 'EPSG:4326'
  }
}

export type Bounds = [west: number, south: number, east: number, north: number]

export const SOURCE_LAYER_API_NAMES: readonly SourceLayerApiName[] = [
  'landuse',
  'water',
  'buildings',
  'roads',
]

export const EMPTY_FEATURE_COLLECTION: GeoJsonFeatureCollection = {
  type: 'FeatureCollection',
  features: [],
}

export const SOURCE_LAYER_CONFIG: ReadonlyArray<{
  key: SourceLayerKey
  label: string
  color: string
}> = [
  { key: 'boundary', label: 'Граница проекта', color: '#f59e0b' },
  { key: 'roads', label: 'Дороги', color: '#334155' },
  { key: 'buildings', label: 'Здания', color: '#d97706' },
  { key: 'water', label: 'Вода', color: '#0ea5e9' },
  { key: 'landuse', label: 'Землепользование', color: '#65a30d' },
]

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i

export function isUuid(value: string): boolean {
  return UUID_RE.test(value.trim())
}

export function viewportBounds(
  west: number,
  south: number,
  east: number,
  north: number,
): Bounds {
  const safeSouth = Math.max(-85, Math.min(85, south))
  const safeNorth = Math.max(-85, Math.min(85, north))
  const safeWest = Math.max(-180, Math.min(180, west))
  const safeEast = Math.max(-180, Math.min(180, east))

  if (safeWest >= safeEast || safeSouth >= safeNorth) {
    return [-179.999, -85, 179.999, 85]
  }
  return [safeWest, safeSouth, safeEast, safeNorth]
}

export function bboxParam(bounds: Bounds): string {
  return bounds.map((value) => Number(value.toFixed(6))).join(',')
}

export function featureCollectionOf(feature: GeoJsonFeature | null): GeoJsonFeatureCollection {
  return {
    type: 'FeatureCollection',
    features: feature ? [feature] : [],
  }
}

function visitCoordinates(value: unknown, visit: (longitude: number, latitude: number) => void): void {
  if (!Array.isArray(value)) return
  if (
    value.length >= 2 &&
    typeof value[0] === 'number' &&
    typeof value[1] === 'number' &&
    Number.isFinite(value[0]) &&
    Number.isFinite(value[1])
  ) {
    visit(value[0], value[1])
    return
  }
  value.forEach((child) => visitCoordinates(child, visit))
}

export function geometryBounds(geometry: GeoJsonGeometry | null): Bounds | null {
  if (!geometry) return null

  if (geometry.type === 'GeometryCollection') {
    return geometry.geometries.reduce<Bounds | null>(
      (accumulator, item) => mergeBounds(accumulator, geometryBounds(item)),
      null,
    )
  }

  let result: Bounds | null = null
  visitCoordinates(geometry.coordinates, (longitude, latitude) => {
    if (!result) {
      result = [longitude, latitude, longitude, latitude]
      return
    }
    result = [
      Math.min(result[0], longitude),
      Math.min(result[1], latitude),
      Math.max(result[2], longitude),
      Math.max(result[3], latitude),
    ]
  })
  return result
}

export function featureCollectionBounds(collection: GeoJsonFeatureCollection): Bounds | null {
  return collection.features.reduce<Bounds | null>(
    (accumulator, feature) => mergeBounds(accumulator, geometryBounds(feature.geometry)),
    null,
  )
}

export function mergeBounds(first: Bounds | null, second: Bounds | null): Bounds | null {
  if (!first) return second
  if (!second) return first
  return [
    Math.min(first[0], second[0]),
    Math.min(first[1], second[1]),
    Math.max(first[2], second[2]),
    Math.max(first[3], second[3]),
  ]
}

export function featureTitle(layer: SourceLayerKey, feature: GeoJsonFeature): string {
  const properties = feature.properties
  const name = properties.name
  if (typeof name === 'string' && name.trim()) return name

  const sourceId = properties.source_feature_id
  if (typeof sourceId === 'string' && sourceId.trim()) return sourceId

  const classKeys = [
    'road_class',
    'building_class',
    'water_class',
    'landuse_class',
  ]
  for (const key of classKeys) {
    const value = properties[key]
    if (typeof value === 'string' && value.trim()) return value
  }

  return layer === 'boundary' ? 'Граница проекта' : `${layer}: ${feature.id}`
}

export function printableProperty(value: unknown): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
    return String(value)
  }
  try {
    return JSON.stringify(value)
  } catch {
    return String(value)
  }
}
