import test from 'node:test'
import assert from 'node:assert/strict'

import {
  LAYER_CATALOG_SCHEMA_VERSION,
  LAYER_REGISTRY,
  UUID_VECTOR_IDS,
  boundedVectorUrl,
  catalogReadUrl,
  layerDefinition,
  layerInstancesForContext,
  legacyBboxUrl,
  mvtTileUrl,
  rasterMetadataUrl,
} from '../src/layerRegistry.ts'
import { MAP_LAYER_STYLES, SOURCE_MAP_LEGEND } from '../src/layerStyles.ts'

const projectId = '11111111-1111-4111-8111-111111111111'
const datasetVersionId = '22222222-2222-4222-8222-222222222222'
const runId = '33333333-3333-4333-8333-333333333333'
const artifactId = '44444444-4444-4444-8444-444444444444'
const bounds = [37, 55, 38, 56]

function select(context = {}) {
  return layerInstancesForContext({ projectId, datasetVersionId, runId, suitabilityArtifactId: artifactId, ...context })
}
function entry(id, context) {
  const found = (context ? select(context) : select()).find((value) => value.definition.id === id)
  assert.ok(found, id)
  return found
}

test('frontend mirrors exactly 18 canonical layer-catalog-v1 definitions in z order', () => {
  assert.equal(LAYER_CATALOG_SCHEMA_VERSION, 'layer-catalog-v1')
  assert.deepEqual(LAYER_REGISTRY.map(({ id }) => id), [
    'analysis.suitability', 'project.boundary', 'source.landuse', 'source.water',
    'source.constraints', 'source.facilities', 'source.buildings', 'source.roads',
    'generated.zones', 'generated.demography', 'generated.blocks', 'generated.parcels',
    'generated.buildings', 'generated.roads', 'run.existing_facilities',
    'generated.facilities', 'generated.infrastructure_demand', 'validation.violations',
  ])
  assert.ok(LAYER_REGISTRY.every((definition) => definition.definitionVersion === '1'
    && definition.styleKey === definition.id && definition.legendKey === definition.id))
  assert.deepEqual(LAYER_REGISTRY.map(({ zIndex }) => zIndex),
    [...LAYER_REGISTRY].map(({ zIndex }) => zIndex).sort((a, b) => a - b))
  assert.equal(UUID_VECTOR_IDS.size, 15)
  assert.deepEqual([...UUID_VECTOR_IDS], LAYER_REGISTRY
    .filter(({ deliveryKind, id }) => deliveryKind === 'bbox_geojson' && id !== 'validation.violations')
    .map(({ id }) => id))
  assert.equal(layerDefinition('validation.violations').maxFeatures, 1000)
  assert.equal(layerDefinition('analysis.suitability').deliveryKind, 'artifact_image')
})

test('style registry covers all definitions with unique map layer IDs and stable source legend', () => {
  assert.deepEqual(Object.keys(MAP_LAYER_STYLES), LAYER_REGISTRY.map(({ id }) => id))
  const parts = Object.values(MAP_LAYER_STYLES).flat()
  assert.equal(new Set(parts.map(({ id }) => id)).size, parts.length)
  assert.ok(parts.every(({ paint, type }) => Object.keys(paint).length > 0
    && ['fill', 'line', 'circle', 'raster'].includes(type)))
  assert.deepEqual(SOURCE_MAP_LEGEND.map(({ key }) => key),
    ['boundary', 'roads', 'buildings', 'water', 'landuse'])
  assert.deepEqual(
    SOURCE_MAP_LEGEND.map(({ layerId }) => layerDefinition(layerId).sourceId),
    ['source-boundary', 'source-roads', 'source-buildings', 'source-water', 'source-landuse'],
  )
  assert.deepEqual(MAP_LAYER_STYLES['source.roads'].map(({ id }) => id), ['source-roads-line'])
})

test('exact owner binding is stable, requires project for dataset/run, and never falls back', () => {
  const a = select()
  const b = select()
  assert.deepEqual(a, b)
  assert.equal(a.length, 18)
  assert.equal(new Set(a.map(({ instanceKey }) => instanceKey)).size, 18)
  assert.equal(entry('source.roads').instanceKey, `source.roads@dataset_version:${projectId}:${datasetVersionId}`)
  assert.equal(entry('generated.roads').instanceKey, `generated.roads@run:${projectId}:${runId}`)
  assert.equal(entry('analysis.suitability').instanceKey, `analysis.suitability@artifact:${artifactId}`)
  const otherRun = '55555555-5555-4555-8555-555555555555'
  assert.notEqual(entry('generated.roads').instanceKey, entry('generated.roads', { runId: otherRun }).instanceKey)
  assert.equal(select({ runId: null }).length, 8) // 6 source, boundary, suitability
  assert.equal(select({ datasetVersionId: null }).length, 12) // 10 run, boundary, suitability
  assert.deepEqual(layerInstancesForContext({ suitabilityArtifactId: artifactId })
    .map(({ definition }) => definition.id), ['analysis.suitability'])
  assert.deepEqual(layerInstancesForContext({ projectId }).map(({ definition }) => definition.id),
    ['project.boundary'])
  assert.throws(() => layerInstancesForContext({ datasetVersionId }), /requires projectId/)
  assert.throws(() => layerInstancesForContext({ projectId, runId: 'invalid' }), /must be a UUID/)
})

test('legacy bbox routes retain owner and existing-facility discriminator', () => {
  const url = new URL(legacyBboxUrl('http://localhost:8000/api/v1', entry('source.roads'), bounds))
  assert.equal(url.pathname,
    `/api/v1/projects/${projectId}/dataset-versions/${datasetVersionId}/source-layers/roads/geojson`)
  assert.equal(url.searchParams.get('bbox'), '37,55,38,56')
  assert.equal(url.searchParams.get('limit'), '1500')
  const existingUrl = new URL(legacyBboxUrl('http://localhost:8000/api/v1', entry('run.existing_facilities'), bounds))
  assert.equal(existingUrl.searchParams.get('origin'), 'existing')
  assert.equal(existingUrl.searchParams.get('bbox'), '37,55,38,56')
  assert.equal(new URL(legacyBboxUrl('http://localhost:8000/api/v1', entry('validation.violations'), bounds, 1000))
    .pathname, `/api/v1/projects/${projectId}/validation-runs/${runId}/violations/geojson`)
  assert.throws(() => legacyBboxUrl('/api/v1', entry('validation.violations'), bounds, 1001), /limit/)
  assert.throws(() => legacyBboxUrl('/api/v1', entry('source.roads'), [-181, 0, 1, 1]), /bbox/)
  assert.throws(() => legacyBboxUrl('/api/v1', entry('project.boundary'), bounds), /Not a bbox/)
})

test('generic bbox and MVT share exact owner identity and reject non-UUID layers', () => {
  const road = entry('generated.roads')
  const bboxUrl = new URL(boundedVectorUrl('http://localhost/api/v1', road, bounds, 10))
  assert.equal(bboxUrl.pathname, `/api/v1/projects/${projectId}/vector-layers/generated.roads/geojson`)
  assert.equal(bboxUrl.searchParams.get('run_id'), runId)
  assert.equal(bboxUrl.searchParams.has('dataset_version_id'), false)
  assert.equal(bboxUrl.searchParams.get('presentation_srid'), '4326')
  const sourceTile = new URL(mvtTileUrl('http://localhost/api/v1', entry('source.roads'), 8, 120, 80))
  assert.equal(sourceTile.pathname,
    `/api/v1/projects/${projectId}/vector-layers/source.roads/tiles/8/120/80.mvt`)
  assert.equal(sourceTile.searchParams.get('dataset_version_id'), datasetVersionId)
  assert.equal(sourceTile.searchParams.get('feature_limit'), '500')
  assert.ok(mvtTileUrl('/api/v1', road, '{z}', '{x}', '{y}')
    .includes(`?run_id=${runId}&feature_limit=500`))
  assert.throws(() => mvtTileUrl('/api/v1', road, 17, 0, 0), /zoom/)
  assert.throws(() => mvtTileUrl('/api/v1', road, 8, 256, 0), /XYZ/)
  assert.throws(() => mvtTileUrl('/api/v1', road, 8, 0, 0, 1001), /feature limit/)
  for (const unsupported of ['project.boundary', 'analysis.suitability', 'validation.violations']) {
    assert.throws(() => mvtTileUrl('/api/v1', entry(unsupported), 0, 0, 0), /UUID vector/)
    assert.throws(() => boundedVectorUrl('/api/v1', entry(unsupported), bounds), /UUID vector/)
  }
})

test('artifact metadata/preview are independently identified, never invented project-owned raster', () => {
  const artifact = entry('analysis.suitability')
  assert.equal(catalogReadUrl('/api/v1', artifact), `/api/v1/suitability-artifacts/${artifactId}/preview.png`)
  assert.equal(rasterMetadataUrl('/api/v1', artifact), `/api/v1/suitability-artifacts/${artifactId}`)
  assert.equal(artifact.owner.projectId, undefined)
  assert.throws(() => rasterMetadataUrl('/api/v1', entry('source.water')), /Not a raster/)
})
