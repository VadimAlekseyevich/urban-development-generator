import { test, expect } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), '../..')
const api = process.env.E2E_API_BASE_URL ?? 'http://localhost:8000/api/v1'
const stages = [
  'prepare_snapshot', 'evaluate_constraints', 'suitability', 'zoning', 'roads',
  'blocks_and_parcels', 'buildings', 'demography', 'infrastructure',
  'final_validation', 'metrics', 'persist_manifest',
]

function python(module) {
  return execFileSync('docker', [
    'compose', 'exec', '-T', 'api', 'uv', 'run', '--no-dev', 'python', '-m', module,
  ], { cwd: repoRoot, encoding: 'utf8', timeout: 60_000 })
}

async function checked(response, status) {
  const body = await response.text()
  expect(response.status(), response.url() + ': ' + body.slice(0, 2500)).toBe(status)
  return JSON.parse(body)
}

test('real uploads → ingest worker → EXPANSION → all stages → GIS and provenance', async ({ request }) => {
  test.setTimeout(300_000)
  // Generate bytes, not DB records. All state changes go through public HTTP routes.
  const input = JSON.parse(python('scripts.generation_acceptance_inputs'))
  const config = JSON.parse(python('scripts.generation_smoke_config'))
  // Explicitly exercise the normalized DEM in addition to vector suitability.
  config.stages.suitability.config.suitability.factors.push({
    code: 'slope', weight: 1, normalization: 'INVERTED_MIN_MAX', raw_min: 0, raw_max: 30,
  })
  const revision = execFileSync('git', ['rev-parse', 'HEAD'], { cwd: repoRoot, encoding: 'utf8' }).trim()
  const project = await checked(await request.post(api + '/projects', {
    data: { name: 'E2E unseeded generation ' + Date.now(), working_srid: input.working_srid },
  }), 201)
  const root = api + '/projects/' + project.id
  await checked(await request.put(root + '/boundary', {
    data: { source_srid: 4326, geometry: input.boundary },
  }), 200)

  const ids = []
  for (const kind of ['roads', 'landuse', 'facilities', 'dem']) {
    const raster = kind === 'dem'
    const upload = await checked(await request.post(api + '/uploads', {
      multipart: { file: {
        name: raster ? 'terrain.tif' : kind + '.geojson',
        mimeType: raster ? 'image/tiff' : 'application/geo+json',
        buffer: raster ? Buffer.from(input.dem_base64, 'base64') : Buffer.from(JSON.stringify(input[kind])),
      } },
    }), 201)
    const ingest = await checked(await request.post(root + '/ingests', {
      data: {
        artifact_id: upload.artifact_id, dataset_kind: kind,
        source_format: raster ? 'geotiff' : 'geojson',
      },
    }), 201)
    ids.push(ingest.dataset_version_id)
    let observed
    await expect.poll(async () => {
      observed = await checked(await request.get(root + '/ingests/' + ingest.dataset_version_id), 200)
      return observed.status === 'ready' ||
        (observed.status === 'failed' && !observed.retry_pending)
    }, { timeout: 60_000, intervals: [500, 1000, 2000], message: 'ingest ' + kind }).toBe(true)
    expect(observed.status, JSON.stringify(observed)).toBe('ready')
    expect(observed.job_status).toBe('succeeded')
  }

  const run = await checked(await request.post(root + '/runs', {
    data: {
      mode: 'EXPANSION', seed: 20260919, dataset_version_ids: ids,
      config_schema_version: config.schema_version, config_json: config, commit_sha: revision,
    },
  }), 201)
  let result
  await expect.poll(async () => {
    result = await checked(await request.get(root + '/runs/' + run.id), 200)
    return ['succeeded', 'failed', 'cancelled'].includes(result.status)
  }, { timeout: 90_000, intervals: [500, 1000, 2000], message: 'real generation worker' }).toBe(true)
  expect(result.status, JSON.stringify(result)).toBe('succeeded')
  expect(result.job.status).toBe('succeeded')
  expect(result.stages.map((stage) => stage.stage_name).sort()).toEqual([...stages].sort())
  expect(result.stages.every((stage) => stage.status === 'succeeded')).toBe(true)

  // Use a generous geographic viewport enclosing the tiny UTM fixture.
  const bounds = input.boundary.coordinates[0]
  const xs = bounds.map((point) => point[0]), ys = bounds.map((point) => point[1])
  const bbox = [Math.min(...xs) - 0.001, Math.min(...ys) - 0.001,
    Math.max(...xs) + 0.001, Math.max(...ys) + 0.001].join(',')
  for (const [path, layer] of [
    ['zoning-runs', 'zones'], ['road-runs', 'roads'], ['block-runs', 'blocks'],
    ['block-runs', 'parcels'], ['building-runs', 'buildings'],
    ['infrastructure-runs', 'facilities'],
  ]) {
    const collection = await checked(await request.get(
      root + '/' + path + '/' + run.id + '/' + layer + '/geojson?bbox=' + bbox,
    ), 200)
    expect(collection.features.length, 'nonempty generated ' + layer).toBeGreaterThan(0)
  }
  const metrics = await checked(await request.get(root + '/metric-runs/' + run.id + '/metrics'), 200)
  expect(metrics.run_id).toBe(run.id)
  expect(metrics.metrics.length).toBeGreaterThan(0)
  const provenance = await request.get(root + '/exports/provenance/' + run.id)
  const manifest = await checked(provenance, 200)
  expect(manifest.run.status).toBe('succeeded')
  expect(manifest.code.commit_sha).toBe(revision)
  expect(manifest.datasets.map((item) => item.dataset_version_id).sort()).toEqual([...ids].sort())
  expect(manifest.datasets.find((item) => item.kind === 'dem').derived_artifacts).toHaveLength(1)
  expect(manifest.stages.map((item) => item.name).sort()).toEqual([...stages].sort())
  expect(provenance.headers()['x-provenance-checksum']).toMatch(/^sha256:[0-9a-f]{64}$/)
})
