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

function python(module, ...args) {
  return execFileSync('docker', [
    'compose', 'exec', '-T', 'api', 'uv', 'run', '--no-dev', 'python', '-m', module, ...args,
  ], { cwd: repoRoot, encoding: 'utf8', timeout: 60_000 })
}

async function checked(response, status) {
  const body = await response.text()
  expect(response.status(), response.url() + ': ' + body.slice(0, 2500)).toBe(status)
  return JSON.parse(body)
}

for (const scenario of ['EXPANSION', 'FROM_SCRATCH', 'PLANNING_PROFILE', 'DENSE_PROFILE']) {
const mode = scenario === 'EXPANSION' ? 'EXPANSION' : 'FROM_SCRATCH'
test('real uploads → ingest worker → ' + scenario + ' → all stages → GIS and provenance', async ({ request, page }) => {
  test.setTimeout(300_000)
  // Generate bytes, not DB records. All state changes go through public HTTP routes.
  const input = JSON.parse(python('scripts.generation_acceptance_inputs', '--size-m', ['PLANNING_PROFILE', 'DENSE_PROFILE'].includes(scenario) ? '400' : mode === 'EXPANSION' ? '10' : '100'))
  let config = JSON.parse(python('scripts.generation_smoke_config'))
  // Explicitly exercise the normalized DEM in addition to vector suitability.
  config.stages.suitability.config.suitability.factors.push({
    code: 'slope', weight: 1, normalization: 'INVERTED_MIN_MAX', raw_min: 0, raw_max: 30,
  })
  if (scenario === 'FROM_SCRATCH') {
    // A 20 m road-routing grid avoids over-dense 10 m triangular blocks.
    // Keep the strict nonempty layer checks; tolerances below reflect discrete
    // footprint placement rather than the exact 10 m square smoke fixture.
    config.stages.prepare_snapshot.config.cell_size_m = 20
    config.stages.roads.config.anchor_policy.max_candidates = 36
    config.stages.roads.config.anchor_policy.max_sampled_cells = 200
    config.stages.roads.config.growth_policy.max_iterations = 40
    config.stages.buildings.config.candidate_policy.grid_spacing_m = 2
    config.stages.buildings.config.zone_targets[0].coverage_tolerance = 0.04
    config.stages.buildings.config.zone_targets[0].far_tolerance = 0.08
    config.stages.roads.config.fixed_attachment_policy = null
    config.stages.blocks_and_parcels.config.split_policy.max_area_m2 = 500
  }
  const revision = execFileSync('git', ['rev-parse', 'HEAD'], { cwd: repoRoot, encoding: 'utf8' }).trim()
  const project = await checked(await request.post(api + '/projects', {
    data: { name: 'E2E unseeded ' + scenario + ' ' + Date.now(), working_srid: input.working_srid },
  }), 201)
  const root = api + '/projects/' + project.id
  await checked(await request.put(root + '/boundary', {
    data: { source_srid: 4326, geometry: input.boundary },
  }), 200)

  const ids = []
  for (const kind of (mode === 'EXPANSION'
    ? ['roads', 'landuse', 'facilities', 'dem'] : ['landuse', 'dem'])) {
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

  if (scenario === 'PLANNING_PROFILE') {
    // Use actual form/preview, not the test-only JSON or a seeded result.
    await page.route('https://demotiles.maplibre.org/style.json', route => route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({ version: 8, sources: {}, layers: [
        { id: 'background', type: 'background', paint: { 'background-color': '#e8eef4' } },
      ] }),
    }))
    await page.goto('/?project_id=' + project.id)
    await expect(page.getByText('API: online')).toBeVisible()
    await page.getByRole('button', { name: 'Генерация' }).click()
    const planning = page.locator('.planning-config')
    await planning.getByLabel('Размер ячейки, м').fill('40')
    await planning.getByLabel('Целевое население, чел.').fill('300')
    const preview = page.waitForResponse(response =>
      response.url().endsWith('/generation/planning-config') && response.request().method() === 'POST')
    await planning.getByRole('button', { name: 'Сформировать конфигурацию' }).click()
    config = await (await preview).json()
    expect(config.schema_version).toBe('canonical-stage-configs-v5')
    expect(config.stages.roads.config.anchor_policy.spatially_balanced).toBe(true)
    await expect(page.locator('.run-form textarea')).toHaveValue(JSON.stringify(config, null, 2))
  }

  if (scenario === 'DENSE_PROFILE') {
    // User-reported seed 42; default-like grid, then force actual shortlist overflow.
    // Test-only smaller budget ensures this fixture exercises the failure path.
    config = await checked(await request.post(api + '/generation/planning-config', {
      data: { cell_size_m: 10, target_population: 1000 },
    }), 200)
    config.stages.infrastructure.config.candidate_policy.max_results = 4000
  }

  const runtime = await checked(await request.get(api + '/generation/revision'), 200)
  expect(runtime.commit_sha).toBe(revision)
  const mismatch = await request.post(root + '/runs', {
    data: {
      mode, seed: scenario === 'DENSE_PROFILE' ? 42 : 20260919, dataset_version_ids: ids,
      config_schema_version: config.schema_version, config_json: config,
      commit_sha: revision === 'a'.repeat(40) ? 'b'.repeat(40) : 'a'.repeat(40),
    },
  })
  expect(mismatch.status()).toBe(409)
  const before = await checked(await request.get(root + '/runs'), 200)
  expect(before.runs).toHaveLength(0)
  const payload = {
    mode, seed: scenario === 'DENSE_PROFILE' ? 42 : 20260919, dataset_version_ids: ids,
    config_schema_version: config.schema_version, config_json: config, commit_sha: revision,
  }
  let run
  if (scenario === 'PLANNING_PROFILE') {
    const form = page.locator('.run-form')
    await form.getByLabel('Режим').selectOption(mode)
    await form.getByLabel('Seed', { exact: true }).fill(String(payload.seed))
    await expect(form.locator('input[pattern="[0-9a-f]{40}"]')).toHaveValue(revision)
    await form.getByRole('checkbox', { name: /dem.*ready/ }).check()
    await form.getByRole('checkbox', { name: /landuse.*ready/ }).check()
    const created = page.waitForResponse(response =>
      response.url().endsWith('/projects/' + project.id + '/runs') && response.request().method() === 'POST')
    await form.getByRole('button', { name: 'Создать запуск' }).click()
    const response = await created
    expect(response.status(), await response.text()).toBe(201)
    const sent = response.request().postDataJSON()
    expect(sent.config_json).toEqual(config)
    expect(sent.dataset_version_ids.sort()).toEqual([...ids].sort())
    run = await response.json()
  } else {
    run = await checked(await request.post(root + '/runs', { data: payload }), 201)
  }
  let result
  await expect.poll(async () => {
    result = await checked(await request.get(root + '/runs/' + run.id), 200)
    return ['succeeded', 'failed', 'cancelled'].includes(result.status)
  }, { timeout: scenario === 'DENSE_PROFILE' ? 180_000 : 90_000, intervals: [500, 1000, 2000], message: 'real generation worker' }).toBe(true)
  expect(result.status, JSON.stringify(result)).toBe('succeeded')
  expect(result.job.status).toBe('succeeded')
  expect(result.stages.map((stage) => stage.stage_name).sort()).toEqual([...stages].sort())
  expect(result.stages.every((stage) => stage.status === 'succeeded')).toBe(true)

  if (scenario === 'DENSE_PROFILE') {
    const diagnostics = result.stages.find(stage => stage.stage_name === 'infrastructure').diagnostics
    console.log('DENSE_PROFILE infrastructure diagnostics: ' + JSON.stringify(diagnostics))
    const budget = diagnostics.find(item => item.code === 'infrastructure.candidate_budget')
    expect(budget, 'must exercise real overflow, not only successful small routing').toBeDefined()
    const match = budget.message.match(/selected (\d+) of (\d+) candidates for (\d+) demand rows/)
    expect(match).not.toBeNull()
    const [, kept, offered, demands] = match.map(Number)
    expect(offered * demands).toBeGreaterThan(4000)
    expect(kept * demands).toBeLessThanOrEqual(4000)
    expect(kept).toBeGreaterThan(0)
  }
  // Use a generous geographic viewport enclosing the tiny UTM fixture.
  const bounds = input.boundary.coordinates[0]
  const xs = bounds.map((point) => point[0]), ys = bounds.map((point) => point[1])
  const bbox = [Math.min(...xs) - 0.001, Math.min(...ys) - 0.001,
    Math.max(...xs) + 0.001, Math.max(...ys) + 0.001].join(',')
  const layers = [
    ['zoning-runs', 'zones'], ['road-runs', 'roads'], ['block-runs', 'blocks'],
    ['block-runs', 'parcels'], ['building-runs', 'buildings'],
    ['infrastructure-runs', 'facilities'],
  ]
  const geometries = new Map()
  for (const [path, layer] of layers) {
    const collection = await checked(await request.get(
      root + '/' + path + '/' + run.id + '/' + layer + '/geojson?limit=5000&bbox=' + bbox,
    ), 200)
    console.log(scenario + ' ' + layer + ': ' + collection.features.length + ' features')
    expect(collection.truncated, 'complete ' + layer + ' viewport').toBe(false)
    geometries.set(layer, collection.features.map(feature => JSON.stringify(feature.geometry)).sort())
    expect(collection.features.length, 'nonempty generated ' + layer).toBeGreaterThan(0)
  }
  const metrics = await checked(await request.get(root + '/metric-runs/' + run.id + '/metrics'), 200)
  expect(metrics.run_id).toBe(run.id)
  expect(metrics.metrics.length).toBeGreaterThan(0)
  expect(metrics.raw_metrics_available).toBe(true)
  const raw = Object.fromEntries(metrics.raw_metrics.map(row => [row.metric_id, row.raw_value]))
  for (const id of ['demography.total_population', 'buildings.far',
    'roads.length_density_km_per_km2', 'infrastructure.population_coverage_ratio']) {
    expect(raw[id], id).toEqual(expect.any(Number))
  }
  expect(raw['demography.total_population']).toBeGreaterThan(0)
  console.log(scenario + ' actual scalar metrics: ' + JSON.stringify(raw))
  const provenance = await request.get(root + '/exports/provenance/' + run.id)
  const manifest = await checked(provenance, 200)
  expect(manifest.run.status).toBe('succeeded')
  expect(manifest.run.mode).toBe(mode)
  expect(manifest.code.commit_sha).toBe(revision)
  expect(manifest.datasets.map((item) => item.dataset_version_id).sort()).toEqual([...ids].sort())
  expect(manifest.datasets.find((item) => item.kind === 'dem').derived_artifacts).toHaveLength(1)
  expect(manifest.stages.map((item) => item.name).sort()).toEqual([...stages].sort())
  expect(provenance.headers()['x-provenance-checksum']).toMatch(/^sha256:[0-9a-f]{64}$/)

  if (scenario === 'FROM_SCRATCH') {
    // Re-run the same immutable version IDs, config, revision and seed in a
    // new GenerationRun. Run IDs and provenance timestamps must differ;
    // geometry multisets must not.
    const replay = await checked(await request.post(root + '/runs', { data: payload }), 201)
    expect(replay.id).not.toBe(run.id)
    let replayState
    await expect.poll(async () => {
      replayState = await checked(await request.get(root + '/runs/' + replay.id), 200)
      return ['succeeded', 'failed', 'cancelled'].includes(replayState.status)
    }, { timeout: 90_000, intervals: [500, 1000, 2000] }).toBe(true)
    expect(replayState.status, JSON.stringify(replayState)).toBe('succeeded')
    for (const [path, layer] of layers) {
      const collection = await checked(await request.get(
        root + '/' + path + '/' + replay.id + '/' + layer + '/geojson?bbox=' + bbox,
      ), 200)
      expect(collection.truncated).toBe(false)
      expect(collection.features.map(feature => JSON.stringify(feature.geometry)).sort(),
        'same-seed geometry: ' + layer).toEqual(geometries.get(layer))
    }
    const replayMetrics = await checked(await request.get(root + '/metric-runs/' + replay.id + '/metrics'), 200)
    expect(replayMetrics.metrics).toEqual(metrics.metrics)
    expect(replayMetrics.raw_metrics).toEqual(metrics.raw_metrics)
    expect(replayMetrics.composite_score).toBe(metrics.composite_score)
    console.log('FROM_SCRATCH: same seed geometry and score replay passed')
  }
  if (scenario === 'PLANNING_PROFILE') {
    // No reload after execution. The layer catalog must discover the new run.
    const detail = page.locator('.run-detail')
    await expect(detail.locator('.run-status')).toHaveText('Завершён', { timeout: 30_000 })
    await expect(detail).toContainText(run.id.slice(0, 8))
    const buildingResponse = page.waitForResponse(response =>
      response.url().includes('/building-runs/' + run.id + '/buildings/geojson') && response.status() === 200)
    await detail.getByRole('button', { name: 'Показать на карте' }).click()
    const panel = page.locator('.buildings-panel')
    // Ensure the generated layer is visible even if it defaults to off.
    const toggle = panel.locator('.buildings-layer-row input')
    if (!(await toggle.isChecked())) await toggle.check()
    const mapBuildings = await (await buildingResponse).json()
    expect(mapBuildings.features.length).toBeGreaterThan(0)
    await expect(panel.locator('select')).toHaveValue(run.id)
    await expect(page.locator('.workspace-context-summary')).toContainText(run.id.slice(0, 8))
  }
})
}
