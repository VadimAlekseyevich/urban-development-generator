import test from 'node:test'
import assert from 'node:assert/strict'
import {
  readWorkspaceSelection, readWorkspaceView, selectProject, selectDatasetVersion,
  selectMapRun, workspaceSearch,
} from '../src/workspaceContext.ts'

const p = '11111111-1111-4111-8111-111111111111'
const q = '22222222-2222-4222-8222-222222222222'
const version = '33333333-3333-4333-8333-333333333333'
const run = '44444444-4444-4444-8444-444444444444'

test('URL does not bind dataset/run when project is missing or invalid', () => {
  assert.deepEqual(readWorkspaceSelection('?dataset_version_id=' + version + '&map_run_id=' + run), {
    projectId: null, datasetVersionId: null, runId: null,
  })
  assert.deepEqual(readWorkspaceSelection('?project_id=nope&map_run_id=' + run), {
    projectId: null, datasetVersionId: null, runId: null,
  })
})

test('workspace switching resets all owner-qualified downstream identities', () => {
  const initial = { projectId: p, datasetVersionId: version, runId: run }
  assert.deepEqual(selectProject(initial, q), { projectId: q, datasetVersionId: null, runId: null })
  assert.deepEqual(selectProject(initial, p), initial)
  assert.deepEqual(selectDatasetVersion(initial, version), {
    projectId: p, datasetVersionId: version, runId: null,
  })
  assert.deepEqual(selectDatasetVersion(initial, null), {
    projectId: p, datasetVersionId: null, runId: null,
  })
  assert.deepEqual(selectMapRun(initial, null), { ...initial, runId: null })
  assert.deepEqual(selectDatasetVersion(selectProject(initial, null), version), {
    projectId: null, datasetVersionId: null, runId: null,
  })
})

test('deep links preserve unrelated query keys and restore explicit shared run pin', () => {
  const state = { projectId: p, datasetVersionId: version, runId: run }
  const search = workspaceSearch('?custom=keep&project_id=' + q, state, 'analysis')
  const restored = readWorkspaceSelection(search)
  assert.deepEqual(restored, state)
  assert.equal(readWorkspaceView(search), 'analysis')
  assert.equal(new URLSearchParams(search).get('custom'), 'keep')
  const changed = workspaceSearch(search, selectProject(state, q), 'data')
  assert.equal(new URLSearchParams(changed).has('map_run_id'), false)
  assert.equal(new URLSearchParams(changed).has('dataset_version_id'), false)
  assert.equal(readWorkspaceView('?workspace_view=invalid'), 'data')
})

test('workspace panels mount persistently and share the catalog and run pin', async () => {
  const { readFileSync } = await import('node:fs')
  const app = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8')
  assert.match(app, /<WorkspaceTabs/)
  assert.match(app, /<ProjectPanel/)
  assert.match(app, /<UploadPanel/)
  assert.match(app, /<RunsPanel/)
  assert.match(app, /<MetricsDashboard/)
  assert.match(app, /<ComparePanel/)
  assert.match(app, /<LayerTree/)
  assert.match(app, /<ZoningPanel/)
  assert.match(app, /<InfrastructurePanel/)
  assert.match(app, /workspace-section.*hidden=\{view !== 'map'\}/)
  assert.match(app, /runId: mapRunId/)
  assert.match(app, /onMapRunChange=\{handleMapRunChange\}/)
  assert.doesNotMatch(app, /setMapRunId/)
})
