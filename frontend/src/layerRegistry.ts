/** Frontend mirror of layer-catalog-v1's 18 logical IDs and owner scopes.
 * This is presentation metadata, never an availability/authorization claim.
 * Backend LayerCatalog and its owner-checked read services remain authoritative.
 */
export const LAYER_CATALOG_SCHEMA_VERSION = 'layer-catalog-v1'
export type LayerOwnerScope = 'project' | 'dataset_version' | 'run' | 'artifact'
export type LayerSourceKind = 'source' | 'generated' | 'validation' | 'analysis'
export type LayerGeometryKind = 'point' | 'line' | 'polygon' | 'mixed' | 'raster'
export type LayerDeliveryKind = 'project_geojson' | 'bbox_geojson' | 'artifact_image'

export type LayerDescriptor = Readonly<{
  id: string
  definitionVersion: '1'
  ownerScope: LayerOwnerScope
  sourceKind: LayerSourceKind
  geometryKind: LayerGeometryKind
  deliveryKind: LayerDeliveryKind
  route: string
  sourceId: string
  zIndex: number
  defaultVisible: boolean
  defaultOpacity: number
  maxFeatures: number | null
  styleKey: string
  legendKey: string
}>

type DefinitionArgs = Omit<LayerDescriptor, 'definitionVersion' | 'styleKey' | 'legendKey'>
function define(args: DefinitionArgs): LayerDescriptor {
  return Object.freeze({ ...args, definitionVersion: '1', styleKey: args.id, legendKey: args.id })
}
const SOURCE_PREFIX = '/projects/{project_id}/dataset-versions/{dataset_version_id}/source-layers/'
const RUN_PREFIX = '/projects/{project_id}/'

/** Canonical backend definition order (and its render z order); no inferred data readiness. */
export const LAYER_REGISTRY = Object.freeze([
  define({ id: 'analysis.suitability', ownerScope: 'artifact', sourceKind: 'analysis', geometryKind: 'raster', deliveryKind: 'artifact_image', route: '/suitability-artifacts/{artifact_id}/preview.png', sourceId: 'suitability-preview', zIndex: 5, defaultVisible: false, defaultOpacity: 0.65, maxFeatures: null }),
  define({ id: 'project.boundary', ownerScope: 'project', sourceKind: 'source', geometryKind: 'polygon', deliveryKind: 'project_geojson', route: '/projects/{project_id}/boundary/geojson', sourceId: 'source-boundary', zIndex: 10, defaultVisible: true, defaultOpacity: 1, maxFeatures: null }),
  define({ id: 'source.landuse', ownerScope: 'dataset_version', sourceKind: 'source', geometryKind: 'polygon', deliveryKind: 'bbox_geojson', route: SOURCE_PREFIX + 'landuse/geojson', sourceId: 'source-landuse', zIndex: 20, defaultVisible: true, defaultOpacity: 0.45, maxFeatures: 5000 }),
  define({ id: 'source.water', ownerScope: 'dataset_version', sourceKind: 'source', geometryKind: 'mixed', deliveryKind: 'bbox_geojson', route: SOURCE_PREFIX + 'water/geojson', sourceId: 'source-water', zIndex: 30, defaultVisible: true, defaultOpacity: 0.8, maxFeatures: 5000 }),
  define({ id: 'source.constraints', ownerScope: 'dataset_version', sourceKind: 'source', geometryKind: 'mixed', deliveryKind: 'bbox_geojson', route: SOURCE_PREFIX + 'constraints/geojson', sourceId: 'source-constraints', zIndex: 35, defaultVisible: false, defaultOpacity: 1, maxFeatures: 5000 }),
  define({ id: 'source.facilities', ownerScope: 'dataset_version', sourceKind: 'source', geometryKind: 'mixed', deliveryKind: 'bbox_geojson', route: SOURCE_PREFIX + 'facilities/geojson', sourceId: 'source-facilities', zIndex: 40, defaultVisible: false, defaultOpacity: 1, maxFeatures: 5000 }),
  define({ id: 'source.buildings', ownerScope: 'dataset_version', sourceKind: 'source', geometryKind: 'polygon', deliveryKind: 'bbox_geojson', route: SOURCE_PREFIX + 'buildings/geojson', sourceId: 'source-buildings', zIndex: 50, defaultVisible: true, defaultOpacity: 0.8, maxFeatures: 5000 }),
  define({ id: 'source.roads', ownerScope: 'dataset_version', sourceKind: 'source', geometryKind: 'line', deliveryKind: 'bbox_geojson', route: SOURCE_PREFIX + 'roads/geojson', sourceId: 'source-roads', zIndex: 60, defaultVisible: true, defaultOpacity: 1, maxFeatures: 5000 }),
  define({ id: 'generated.zones', ownerScope: 'run', sourceKind: 'generated', geometryKind: 'polygon', deliveryKind: 'bbox_geojson', route: RUN_PREFIX + 'zoning-runs/{run_id}/zones/geojson', sourceId: 'zoning-generated-source', zIndex: 100, defaultVisible: true, defaultOpacity: 0.48, maxFeatures: 5000 }),
  define({ id: 'generated.demography', ownerScope: 'run', sourceKind: 'generated', geometryKind: 'polygon', deliveryKind: 'bbox_geojson', route: RUN_PREFIX + 'demography-runs/{run_id}/blocks/geojson', sourceId: 'demography-blocks-ui-source', zIndex: 110, defaultVisible: false, defaultOpacity: 0.55, maxFeatures: 5000 }),
  define({ id: 'generated.blocks', ownerScope: 'run', sourceKind: 'generated', geometryKind: 'polygon', deliveryKind: 'bbox_geojson', route: RUN_PREFIX + 'block-runs/{run_id}/blocks/geojson', sourceId: 'blocks-ui-source', zIndex: 120, defaultVisible: true, defaultOpacity: 0.4, maxFeatures: 5000 }),
  define({ id: 'generated.parcels', ownerScope: 'run', sourceKind: 'generated', geometryKind: 'polygon', deliveryKind: 'bbox_geojson', route: RUN_PREFIX + 'block-runs/{run_id}/parcels/geojson', sourceId: 'parcels-ui-source', zIndex: 130, defaultVisible: true, defaultOpacity: 0.3, maxFeatures: 5000 }),
  define({ id: 'generated.buildings', ownerScope: 'run', sourceKind: 'generated', geometryKind: 'polygon', deliveryKind: 'bbox_geojson', route: RUN_PREFIX + 'building-runs/{run_id}/buildings/geojson', sourceId: 'generated-buildings-ui-source', zIndex: 140, defaultVisible: true, defaultOpacity: 0.8, maxFeatures: 5000 }),
  define({ id: 'generated.roads', ownerScope: 'run', sourceKind: 'generated', geometryKind: 'line', deliveryKind: 'bbox_geojson', route: RUN_PREFIX + 'road-runs/{run_id}/roads/geojson', sourceId: 'roads-ui-generated-source', zIndex: 150, defaultVisible: true, defaultOpacity: 1, maxFeatures: 5000 }),
  define({ id: 'run.existing_facilities', ownerScope: 'run', sourceKind: 'source', geometryKind: 'mixed', deliveryKind: 'bbox_geojson', route: RUN_PREFIX + 'infrastructure-runs/{run_id}/facilities/geojson?origin=existing', sourceId: 'infrastructure-ui-source', zIndex: 160, defaultVisible: true, defaultOpacity: 1, maxFeatures: 5000 }),
  define({ id: 'generated.facilities', ownerScope: 'run', sourceKind: 'generated', geometryKind: 'mixed', deliveryKind: 'bbox_geojson', route: RUN_PREFIX + 'infrastructure-runs/{run_id}/facilities/geojson?origin=generated', sourceId: 'infrastructure-ui-source', zIndex: 170, defaultVisible: true, defaultOpacity: 1, maxFeatures: 5000 }),
  define({ id: 'generated.infrastructure_demand', ownerScope: 'run', sourceKind: 'generated', geometryKind: 'polygon', deliveryKind: 'bbox_geojson', route: RUN_PREFIX + 'infrastructure-runs/{run_id}/demand/geojson', sourceId: 'infrastructure-demand-ui-source', zIndex: 180, defaultVisible: false, defaultOpacity: 0.55, maxFeatures: 5000 }),
  define({ id: 'validation.violations', ownerScope: 'run', sourceKind: 'validation', geometryKind: 'mixed', deliveryKind: 'bbox_geojson', route: RUN_PREFIX + 'validation-runs/{run_id}/violations/geojson', sourceId: 'validation-violations', zIndex: 200, defaultVisible: true, defaultOpacity: 1, maxFeatures: 1000 }),
] satisfies readonly LayerDescriptor[])

export type LayerId =
  | 'analysis.suitability' | 'project.boundary'
  | 'source.landuse' | 'source.water' | 'source.constraints' | 'source.facilities'
  | 'source.buildings' | 'source.roads'
  | 'generated.zones' | 'generated.demography' | 'generated.blocks'
  | 'generated.parcels' | 'generated.buildings' | 'generated.roads'
  | 'run.existing_facilities' | 'generated.facilities'
  | 'generated.infrastructure_demand' | 'validation.violations'

export function layerDefinition(id: LayerId): LayerDescriptor {
  const descriptor = LAYER_REGISTRY.find((item) => item.id === id)
  if (!descriptor) throw new Error(`Unknown canonical layer: ${id}`)
  return descriptor
}

export type LayerSelection = Readonly<{
  projectId?: string | null
  datasetVersionId?: string | null
  runId?: string | null
  suitabilityArtifactId?: string | null
}>
export type LayerInstance = Readonly<{
  definition: LayerDescriptor
  owner: Readonly<{
    scope: LayerOwnerScope
    projectId?: string
    datasetVersionId?: string
    runId?: string
    artifactId?: string
  }>
  instanceKey: string
}>
const UUID_PATTERN = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i
function normalizedUuid(value: string | null | undefined, name: string): string | undefined {
  if (value == null || value === '') return undefined
  if (!UUID_PATTERN.test(value)) throw new Error(`${name} must be a UUID`)
  return value.toLowerCase()
}
/** Never silently fall back from a missing pinned run to another run. */
export function layerInstancesForContext(selection: LayerSelection): readonly LayerInstance[] {
  const projectId = normalizedUuid(selection.projectId, 'projectId')
  const datasetVersionId = normalizedUuid(selection.datasetVersionId, 'datasetVersionId')
  const runId = normalizedUuid(selection.runId, 'runId')
  const artifactId = normalizedUuid(selection.suitabilityArtifactId, 'suitabilityArtifactId')
  if (!projectId && (datasetVersionId || runId)) throw new Error('dataset/run requires projectId')
  return LAYER_REGISTRY.flatMap((definition): LayerInstance[] => {
    let owner: LayerInstance['owner'] | undefined
    if (definition.ownerScope === 'project' && projectId) owner = { scope: 'project', projectId }
    if (definition.ownerScope === 'dataset_version' && projectId && datasetVersionId) {
      owner = { scope: 'dataset_version', projectId, datasetVersionId }
    }
    if (definition.ownerScope === 'run' && projectId && runId) {
      owner = { scope: 'run', projectId, runId }
    }
    if (definition.ownerScope === 'artifact' && artifactId) owner = { scope: 'artifact', artifactId }
    if (!owner) return []
    const identity = [owner.projectId, owner.datasetVersionId, owner.runId, owner.artifactId]
      .filter((part) => part !== undefined).join(':')
    return [{ definition, owner, instanceKey: `${definition.id}@${owner.scope}:${identity}` }]
  })
}
function apiPath(apiBase: string, instance: LayerInstance): string {
  const owner = instance.owner
  return apiBase.replace(/\/$/, '') + instance.definition.route.replace(
    /\{(project_id|dataset_version_id|run_id|artifact_id)\}/g,
    (_full, name: string) => {
      const value = name === 'project_id' ? owner.projectId
        : name === 'dataset_version_id' ? owner.datasetVersionId
        : name === 'run_id' ? owner.runId : owner.artifactId
      if (!value) throw new Error(`Missing ${name} for ${instance.instanceKey}`)
      return encodeURIComponent(value)
    },
  )
}
export function catalogReadUrl(apiBase: string, instance: LayerInstance): string {
  return apiPath(apiBase, instance)
}
export function rasterMetadataUrl(apiBase: string, instance: LayerInstance): string {
  if (instance.definition.id !== 'analysis.suitability') throw new Error('Not a raster artifact')
  return apiPath(apiBase, instance).replace(/\/preview\.png$/, '')
}
function appendParams(path: string, params: URLSearchParams): string {
  return path + (path.includes('?') ? '&' : '?') + params.toString()
}
function bboxValue(bounds: readonly [number, number, number, number]): string {
  const [west, south, east, north] = bounds
  if (!bounds.every(Number.isFinite) || west < -180 || east > 180
    || south < -90 || north > 90 || west >= east || south >= north) {
    throw new Error('bbox must be a valid EPSG:4326 viewport')
  }
  return bounds.join(',')
}
/** Specialized GeoJSON paths, including canonical non-UUID validation indices. */
export function legacyBboxUrl(
  apiBase: string, instance: LayerInstance,
  bounds: readonly [number, number, number, number], limit = 1500,
): string {
  if (instance.definition.deliveryKind !== 'bbox_geojson') throw new Error('Not a bbox layer')
  if (!Number.isInteger(limit) || limit < 1 || limit > (instance.definition.maxFeatures ?? 0)) {
    throw new Error('Invalid layer feature limit')
  }
  return appendParams(apiPath(apiBase, instance), new URLSearchParams({
    bbox: bboxValue(bounds), limit: String(limit),
  }))
}
export const UUID_VECTOR_IDS: ReadonlySet<string> = new Set(
  LAYER_REGISTRY.filter((d) => d.deliveryKind === 'bbox_geojson'
    && d.id !== 'validation.violations').map((d) => d.id),
)
function requireTableVector(instance: LayerInstance): void {
  if (!UUID_VECTOR_IDS.has(instance.definition.id)) {
    throw new Error('Layer has no table-backed UUID vector read model')
  }
}
function ownerQuery(instance: LayerInstance): string {
  if (instance.owner.scope === 'dataset_version' && instance.owner.datasetVersionId) {
    return `dataset_version_id=${encodeURIComponent(instance.owner.datasetVersionId)}`
  }
  if (instance.owner.scope === 'run' && instance.owner.runId) {
    return `run_id=${encodeURIComponent(instance.owner.runId)}`
  }
  throw new Error('Table vector requires an exact version/run owner')
}
export function boundedVectorUrl(
  apiBase: string, instance: LayerInstance,
  bounds: readonly [number, number, number, number], limit = 1000,
  after?: string,
): string {
  requireTableVector(instance)
  if (!Number.isInteger(limit) || limit < 1 || limit > 5000) throw new Error('Invalid vector limit')
  const params = new URLSearchParams({
    bbox: bboxValue(bounds), limit: String(limit),
    presentation_srid: '4326', simplify_m: '0',
  })
  if (after) params.set('after', normalizedUuid(after, 'after') ?? '')
  return `${apiBase.replace(/\/$/, '')}/projects/${encodeURIComponent(instance.owner.projectId!)}/vector-layers/${encodeURIComponent(instance.definition.id)}/geojson?${ownerQuery(instance)}&${params.toString()}`
}
export function mvtTileUrl(
  apiBase: string, instance: LayerInstance,
  z: number | '{z}', x: number | '{x}', y: number | '{y}',
  featureLimit = 500,
): string {
  requireTableVector(instance)
  if (!Number.isInteger(featureLimit) || featureLimit < 1 || featureLimit > 1000) {
    throw new Error('Invalid MVT feature limit')
  }
  if (typeof z === 'number' && (!Number.isInteger(z) || z < 0 || z > 16)) {
    throw new Error('Invalid MVT zoom')
  }
  if (typeof z === 'number' && typeof x === 'number' && typeof y === 'number'
    && (!Number.isInteger(x) || !Number.isInteger(y)
      || x < 0 || y < 0 || x >= 2 ** z || y >= 2 ** z)) {
    throw new Error('Invalid MVT XYZ')
  }
  if (typeof z === 'number' && (typeof x !== 'number' || typeof y !== 'number')) {
    throw new Error('Numeric MVT zoom requires numeric x/y')
  }
  if (typeof z !== 'number' && (x !== '{x}' || y !== '{y}')) {
    throw new Error('XYZ tile template must use all placeholders')
  }
  return `${apiBase.replace(/\/$/, '')}/projects/${encodeURIComponent(instance.owner.projectId!)}/vector-layers/${encodeURIComponent(instance.definition.id)}/tiles/${z}/${x}/${y}.mvt?${ownerQuery(instance)}&feature_limit=${featureLimit}`
}
