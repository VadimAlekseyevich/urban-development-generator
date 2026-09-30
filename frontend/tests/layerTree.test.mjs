import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

import { LAYER_REGISTRY } from '../src/layerRegistry.ts'
import {
  buildLayerTree,
  layerOwnerCaption,
} from '../src/layerTree.ts'
import {
  getLayerVisibilitySnapshot,
  resetLayerVisibility,
  setLayerVisible,
  subscribeLayerVisibility,
} from '../src/layerVisibility.ts'
import { SOURCE_LAYER_API_NAMES } from '../src/sourceLayers.ts'

const projectId = '11111111-1111-4111-8111-111111111111'
const datasetVersionId = '22222222-2222-4222-8222-222222222222'
const runId = '33333333-3333-4333-8333-333333333333'
const suitabilityArtifactId = '44444444-4444-4444-8444-444444444444'

test('layer tree derives all 18 logical layers from catalog source kind and canonical order', () => {
  resetLayerVisibility()
  const groups = buildLayerTree({}, getLayerVisibilitySnapshot())
  assert.deepEqual(groups.map(({ id }) => id), ['analysis', 'source', 'generated', 'validation'])
  assert.deepEqual(groups.map(({ nodes }) => nodes.length), [1, 8, 8, 1])
  const flattened = groups.flatMap(({ nodes }) => nodes)
  assert.equal(flattened.length, 18)
  assert.deepEqual(
    flattened.map(({ id }) => id).sort(),
    LAYER_REGISTRY.map(({ id }) => id).sort(),
  )
  assert.ok(flattened.every(({ instance }) => instance === null))
})

test('tree binds exact project/version/run/artifact instances without hidden fallback', () => {
  resetLayerVisibility()
  const groups = buildLayerTree(
    { projectId, datasetVersionId, runId, suitabilityArtifactId },
    getLayerVisibilitySnapshot(),
  )
  const nodes = groups.flatMap(({ nodes }) => nodes)
  assert.ok(nodes.every(({ instance }) => instance !== null))
  const sourceRoads = nodes.find(({ id }) => id === 'source.roads')
  const generatedRoads = nodes.find(({ id }) => id === 'generated.roads')
  const suitability = nodes.find(({ id }) => id === 'analysis.suitability')
  assert.equal(sourceRoads?.instance?.owner.datasetVersionId, datasetVersionId)
  assert.equal(generatedRoads?.instance?.owner.runId, runId)
  assert.equal(suitability?.instance?.owner.artifactId, suitabilityArtifactId)
  assert.equal(layerOwnerCaption(generatedRoads?.instance ?? null), 'run 33333333')

  const datasetOnly = buildLayerTree(
    { projectId, datasetVersionId },
    getLayerVisibilitySnapshot(),
  ).flatMap(({ nodes }) => nodes)
  assert.equal(datasetOnly.filter(({ instance }) => instance !== null).length, 7)
  assert.equal(datasetOnly.find(({ id }) => id === 'generated.roads')?.instance, null)
  assert.equal(datasetOnly.find(({ id }) => id === 'run.existing_facilities')?.instance, null)
})

test('one shared visibility store preserves catalog defaults and synchronizes tree/panels', () => {
  resetLayerVisibility()
  const defaults = getLayerVisibilitySnapshot()
  for (const definition of LAYER_REGISTRY) {
    assert.equal(defaults[definition.id], definition.defaultVisible, definition.id)
  }

  let notifications = 0
  const unsubscribe = subscribeLayerVisibility(() => {
    notifications += 1
  })
  setLayerVisible('generated.roads', false)
  assert.equal(getLayerVisibilitySnapshot()['generated.roads'], false)
  setLayerVisible('generated.roads', (current) => !current)
  assert.equal(getLayerVisibilitySnapshot()['generated.roads'], true)
  setLayerVisible('generated.roads', true)
  assert.equal(notifications, 2)
  unsubscribe()
  resetLayerVisibility()
})

test('full source viewport includes all six dataset-backed source layers', () => {
  assert.deepEqual(SOURCE_LAYER_API_NAMES, [
    'landuse',
    'water',
    'constraints',
    'facilities',
    'buildings',
    'roads',
  ])
})

test('root/panel map visibility converges on the full tree and removes duplicate fixed layers', () => {
  const app = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8')
  assert.match(app, /<LayerTree/)
  assert.doesNotMatch(app, /<h2>Исходные слои<\/h2>/)
  assert.match(app, /useLayerVisibilitySnapshot/)

  const zoning = readFileSync(new URL('../src/ZoningPanel.tsx', import.meta.url), 'utf8')
  const roads = readFileSync(new URL('../src/RoadsPanel.tsx', import.meta.url), 'utf8')
  assert.match(zoning, /useLayerVisibility\('generated\.zones'\)/)
  assert.doesNotMatch(zoning, /zoning-fixed-source|zoning-fixed-fill/)
  assert.match(roads, /useLayerVisibility\('generated\.roads'\)/)
  assert.doesNotMatch(roads, /roads-ui-existing-source|roads-ui-existing-line/)

  const canonicalBindings = {
    'BlockParcelsPanel.tsx': ['generated.blocks', 'generated.parcels'],
    'BuildingsPanel.tsx': ['generated.buildings'],
    'DemographyPanel.tsx': ['generated.demography'],
    'InfrastructurePanel.tsx': [
      'run.existing_facilities',
      'generated.facilities',
      'generated.infrastructure_demand',
    ],
    'ViolationsPanel.tsx': ['validation.violations'],
    'SuitabilityPanel.tsx': ['analysis.suitability'],
  }
  for (const [name, ids] of Object.entries(canonicalBindings)) {
    const source = readFileSync(new URL('../src/' + name, import.meta.url), 'utf8')
    for (const id of ids) {
      assert.match(source, new RegExp("useLayerVisibility\\('" + id.replace('.', '\\.') + "'\\)"))
    }
  }
})
