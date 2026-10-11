import { test, expect } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), '../..')
const apiBase = process.env.E2E_API_BASE_URL ?? 'http://localhost:8000/api/v1'
const uploadFile = resolve(repoRoot, 'tests/fixtures/ingest/roads.geojson')

// Only replace the public background map style. ALL application API calls, binary
// upload, SQL/PostGIS reads, outbox run creation, comparison and CSV are real.
const offlineStyle = {
  version: 8,
  name: 'isolated E2E background',
  sources: {},
  layers: [{ id: 'background', type: 'background', paint: { 'background-color': '#e8eef4' } }],
}

test('create → upload → ready fixture → run → inspect → compare → CSV export', async ({ page }) => {
  await page.route('https://demotiles.maplibre.org/style.json', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(offlineStyle) })
  })

  // Actual project creation through browser and API.
  await page.goto('/')
  await expect(page.getByText('API: online')).toBeVisible()
  const projectName = 'E2E Browser Acceptance ' + Date.now()
  const projectForm = page.locator('.workspace-create-project')
  await projectForm.getByText('Название').locator('..').locator('input').fill(projectName)
  await projectForm.getByText('Метрический EPSG / working SRID').locator('..').locator('input').fill('32637')
  await projectForm.getByRole('button', { name: 'Создать проект' }).click()
  await expect(page.locator('.workspace-project-item[aria-pressed="true"]')).toContainText(projectName)

  const projectId = new URL(page.url()).searchParams.get('project_id')
  expect(projectId).toMatch(/^[0-9a-f-]{36}$/)

  // Original File is uploaded via multipart XHR; ready Artifact != DatasetVersion.
  const upload = page.locator('section[aria-label="Загрузка данных"]')
  await upload.locator('input[type="file"]').setInputFiles(uploadFile)
  await upload.getByRole('button', { name: 'Загрузить файл' }).click()
  await expect(upload.getByText('Артефакт сохранён. Он ещё не является версией набора данных.')).toBeVisible()
  await expect(upload.locator('.upload-artifact-details')).toContainText('sha256:')
  await expect(upload.locator('.upload-version-list li')).toHaveCount(0)

  // A guarded canonical PostGIS fixture supplies the missing ingest workflow.
  // This does not promote the uploaded Artifact to a DatasetVersion.
  const fixtureText = execFileSync('docker', [
    'compose', 'exec', '-T', '-e', 'UDG_E2E_FIXTURE=1',
    'api', 'uv', 'run', 'python', 'scripts/seed_browser_e2e.py',
    '--project-id', projectId,
  ], { cwd: repoRoot, encoding: 'utf8', timeout: 60_000 })
  const fixture = JSON.parse(fixtureText.trim())
  expect(fixture.run_ids).toHaveLength(2)

  await upload.getByRole('button', { name: 'Обновить' }).click()
  const readyVersion = upload.locator('.upload-version-list li').filter({ hasText: 'roads · v1' })
  await expect(readyVersion).toBeVisible()
  await expect(readyVersion).toContainText('ready')
  await readyVersion.getByRole('button', { name: 'Выбрать' }).click()
  await expect(page.locator('.workspace-context-summary')).toContainText(fixture.version_id.slice(0, 8))

  // Reload exercises permalink restoration and refreshes the project boundary.
  const roadResponse = page.waitForResponse((response) =>
    response.url().includes('/source-layers/roads/geojson') &&
    response.url().includes(fixture.version_id) && response.status() === 200,
  )
  await page.reload()
  const roads = await (await roadResponse).json()
  expect(roads.features.some((item) => item.properties.name === 'E2E Avenue')).toBe(true)
  await expect(page.locator('section.map-shell')).toBeVisible()
  await expect(page.locator('.layer-tree-panel')).toContainText('Слои карты')

  // Real MapLibre feature selection with a fully offline base style.
  await page.waitForTimeout(650) // the source boundary fitBounds animation is 450ms
  const canvas = page.locator('.maplibregl-canvas')
  await expect(canvas).toBeVisible()
  const bounds = await canvas.boundingBox()
  expect(bounds).not.toBeNull()
  await canvas.click({ position: { x: bounds.width / 2, y: bounds.height / 2 } })
  await expect(page.locator('.inspector-panel')).toContainText('E2E Avenue')

  // Submit a genuine queued generation job; test does not fabricate worker success.
  await page.getByRole('button', { name: 'Генерация' }).click()
  const runForm = page.locator('.run-form')
  await runForm.locator('input[pattern="[0-9a-f]{40}"]').fill('a'.repeat(40))
  const createRunResponse = page.waitForResponse((response) =>
    response.request().method() === 'POST' &&
    response.url().endsWith('/projects/' + projectId + '/runs'),
  )
  await runForm.getByRole('button', { name: 'Создать запуск' }).click()
  const runResponse = await createRunResponse
  expect(runResponse.status()).toBe(201)
  const pendingRun = await runResponse.json()
  expect(pendingRun.project_id).toBe(projectId)
  expect(pendingRun.job?.id).toBeTruthy()
  await expect(page.locator('.run-panel')).toContainText(pendingRun.id.slice(0, 8))

  // Compare two persisted, canonical successful runs without any response mocks.
  await page.getByRole('button', { name: 'Анализ' }).click()
  const compare = page.locator('section.compare-panel')
  await compare.getByRole('button', { name: 'Обновить' }).click()
  await expect(compare.locator('.compare-option')).toHaveCount(2)
  await compare.locator('.compare-option').filter({ hasText: 'seed 71' }).getByRole('checkbox').check()
  await compare.locator('.compare-option').filter({ hasText: 'seed 72' }).getByRole('checkbox').check()
  await compare.getByRole('button', { name: /Сравнить 2/ }).click()
  await expect(compare.locator('.compare-result')).toContainText('Composite score')
  await expect(compare.locator('.compare-result')).toContainText('land.developed_area_m2')
  await expect(page.locator('.workspace-context-summary')).toContainText(fixture.run_ids[0].slice(0, 8))

  const downloadPromise = page.waitForEvent('download')
  await compare.getByRole('button', { name: 'Скачать CSV сравнения' }).click()
  const download = await downloadPromise
  expect(download.suggestedFilename()).toBe('raw-metrics-2-runs.csv')
  const csvText = readFileSync(await download.path(), 'utf8')
  expect(csvText).toContain('land.developed_area_m2')
  expect(csvText).toContain(fixture.run_ids[0])
  expect(csvText).toContain(fixture.run_ids[1])
  expect(csvText).toContain(projectId)
})
