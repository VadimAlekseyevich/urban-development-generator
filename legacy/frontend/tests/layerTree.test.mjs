import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

import {
  LAYER_REGISTRY,
  layerInstancesForContext,
} from '../src/layerRegistry.ts'
import { SOURCE_LAYER_API_NAMES } from '../src/sourceLayers.ts'

const projectId = '11111111-1111-4111-8111-111111111111'
const datasetVersionId = '22222222-2222-4222-8222-222222222222'
const runId = '33333333-3333-4333-8333-333333333333'
const suitabilityArtifactId = '44444444-4444-4444-8444-444444444444'

test('catalog supports complete semantic tree: 1 analysis, 8 source, 8 generated, 1 validation', () => {
  const grouped = Object.groupBy(LAYER_REGISTRY, ({ sourceKind }) => sourceKind)
  assert.deepEqual(
    ['analysis', 'source', 'generated', 'validation'].map(
      (kind) => grouped[kind]?.length ?? 0,
    ),
    [1, 8, 8, 1],
  )
  assert.equal(LAYER_REGISTRY.length, 18)
})

test('tree owner inputs use exact catalog binding without hidden run/version fallback', () => {
  const all = layerInstancesForContext({
    projectId,
    datasetVersionId,
    runId,
    suitabilityArtifactId,
  })
  assert.equal(all.length, 18)
  assert.equal(
    all.find(({ definition }) => definition.id === 'source.roads')?.owner.datasetVersionId,
    datasetVersionId,
  )
  assert.equal(
    all.find(({ definition }) => definition.id === 'generated.roads')?.owner.runId,
    runId,
  )
  assert.equal(
    all.find(({ definition }) => definition.id === 'analysis.suitability')?.owner.artifactId,
    suitabilityArtifactId,
  )
  const datasetOnly = layerInstancesForContext({ projectId, datasetVersionId })
  assert.equal(datasetOnly.length, 7)
  assert.equal(
    datasetOnly.some(({ definition }) => definition.id === 'generated.roads'),
    false,
  )
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

test('tree/store derive membership and defaults from registry rather than a second layer list', () => {
  const treeModel = readFileSync(new URL('../src/layerTree.ts', import.meta.url), 'utf8')
  const visibility = readFileSync(new URL('../src/layerVisibility.ts', import.meta.url), 'utf8')
  const component = readFileSync(new URL('../src/LayerTree.tsx', import.meta.url), 'utf8')
  assert.match(treeModel, /LAYER_REGISTRY\.map/)
  assert.match(treeModel, /layerInstancesForContext\(selection\)/)
  assert.match(visibility, /LAYER_REGISTRY\.map/)
  assert.match(visibility, /definition\.defaultVisible/)
  assert.match(visibility, /useSyncExternalStore/)
  assert.match(component, /buildLayerTree\(selection, visibility\)/)
  assert.match(component, /setLayerVisible\(node\.id, event\.target\.checked\)/)
})

test('root/panel map visibility converges on tree and duplicate fixed layers are removed', () => {
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
      assert.match(
        source,
        new RegExp("useLayerVisibility\\('" + id.replaceAll('.', '\\.') + "'\\)"),
      )
    }
  }
})
