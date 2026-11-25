import { test, expect } from '@playwright/test'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

const dir = process.env.RYAZAN_REPRO_DIR
const api = process.env.E2E_API_BASE_URL ?? 'http://localhost:8000/api/v1'
const read = name => readFileSync(join(dir, name))
const json = name => JSON.parse(read(name).toString('utf8'))

async function checked(response, status) {
  const body = await response.text()
  expect(response.status(), body.slice(0, 2500)).toBe(status)
  return JSON.parse(body)
}

test('Ryazan exact extracted sources, EXPANSION, real worker', async ({ request }) => {
  test.skip(!dir, 'Set RYAZAN_REPRO_DIR to the unpacked local fixture; no source download')
  test.setTimeout(300_000)
  const context = json('context.json')
  const original = json('run-result.json')
  const config = json('planning-config.json')
  const revision = await checked(await request.get(api + '/generation/revision'), 200)
  expect(revision.commit_sha).toMatch(/^[0-9a-f]{40}$/)
  const project = await checked(await request.post(api + '/projects', {
    data: { name: 'Ryazan replay ' + Date.now(), working_srid: context.working_srid },
  }), 201)
  const root = api + '/projects/' + project.id
  await checked(await request.put(root + '/boundary', {
    data: { source_srid: 4326, geometry: json('boundary.geojson') },
  }), 200)
  const ids = []
  for (const name of ['roads', 'buildings', 'landuse', 'water_polygons', 'water_lines']) {
    const filename = name + '.geojson'
    expect(json(filename).features.length).toBe(context.counts[name])
    const upload = await checked(await request.post(api + '/uploads', {
      multipart: { file: {
        name: filename, mimeType: 'application/geo+json', buffer: read(filename),
      } },
    }), 201)
    const ingest = await checked(await request.post(root + '/ingests', {
      data: {
        artifact_id: upload.artifact_id,
        dataset_kind: name.startsWith('water_') ? 'water' : name,
        source_format: 'geojson',
      },
    }), 201)
    ids.push(ingest.dataset_version_id)
    let observed
    await expect.poll(async () => {
      observed = await checked(await request.get(root + '/ingests/' + ingest.dataset_version_id), 200)
      return observed.status === 'ready' ||
        (observed.status === 'failed' && !observed.retry_pending)
    }, { timeout: 60_000 }).toBe(true)
    expect(observed.status, JSON.stringify(observed)).toBe('ready')
  }

  // Exact archived config by default. Opt-in only: compare calibrated growth
  // separately; never silently substitute settings for the original repro.
  if (process.env.RYAZAN_CALIBRATED === '1') {
    const growth = config.stages.roads.config.growth_policy
    growth.max_iterations = 40
    growth.sparse_neighbourhood = true
    config.stages.roads.config.validation_policy.preserve_source_components = true
    config.stages.blocks_and_parcels.config.exclude_pedestrian_boundaries = true
  }
  const run = await checked(await request.post(root + '/runs', {
    data: {
      mode: original.mode, seed: original.seed, dataset_version_ids: ids,
      config_schema_version: config.schema_version, config_json: config,
      commit_sha: revision.commit_sha,
    },
  }), 201)
  let state
  await expect.poll(async () => {
    state = await checked(await request.get(root + '/runs/' + run.id), 200)
    return ['succeeded', 'failed', 'cancelled'].includes(state.status)
  }, { timeout: 180_000 }).toBe(true)
  console.log('RYAZAN_REPLAY ' + JSON.stringify({ project: project.id, run: state }))
  expect(state.status, JSON.stringify(state)).toBe('succeeded')
  expect(state.stages).toHaveLength(12)
  expect(state.stages.every(stage => stage.status === 'succeeded')).toBe(true)
  const metrics = await checked(await request.get(root + '/metric-runs/' + run.id + '/metrics'), 200)
  expect(metrics.raw_metrics_available).toBe(true)
  const values = Object.fromEntries(metrics.raw_metrics.map(row => [row.metric_id, row.raw_value]))
  expect(values['demography.total_population']).toBeGreaterThan(0)
  expect(values['constraints.hard_violation_count']).toBe(0)
  console.log('RYAZAN_METRICS ' + JSON.stringify(values))
})
