import { test, expect } from '@playwright/test'

const offlineStyle = {
  version: 8,
  name: 'deterministic E2E background',
  sources: {},
  layers: [{ id: 'background', type: 'background', paint: {
    'background-color': '#e8eef4',
  } }],
}

test('real upload → persisted Job/Outbox → worker → ready DatasetVersion', async ({ page }) => {
  await page.route('https://demotiles.maplibre.org/style.json', async (route) => {
    await route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify(offlineStyle),
    })
  })
  await page.goto('/')
  await expect(page.getByText('API: online')).toBeVisible()

  const projectName = 'E2E True Ingest ' + Date.now()
  const projectForm = page.locator('.workspace-create-project')
  await projectForm.getByText('Название').locator('..').locator('input').fill(projectName)
  await projectForm.getByText('Метрический EPSG / working SRID')
    .locator('..').locator('input').fill('32637')
  await projectForm.getByRole('button', { name: 'Создать проект' }).click()
  await expect(page.locator('.workspace-project-item[aria-pressed="true"]'))
    .toContainText(projectName)
  const projectId = new URL(page.url()).searchParams.get('project_id')
  expect(projectId).toMatch(/^[0-9a-f-]{36}$/)

  const upload = page.locator('section[aria-label="Загрузка данных"]')
  const realFeatures = {
    type: 'FeatureCollection',
    features: [{
      type: 'Feature',
      properties: { road_class: 'local', name: 'Browser Ingest Road' },
      geometry: { type: 'LineString', coordinates: [
        [37.615, 55.755], [37.625, 55.755],
      ] },
    }],
  }
  await upload.locator('input[type="file"]').setInputFiles({
    name: 'browser-e2e.geojson',
    mimeType: 'application/geo+json',
    buffer: Buffer.from(JSON.stringify(realFeatures)),
  })
  await upload.getByRole('button', { name: 'Загрузить файл' }).click()
  await expect(upload.locator('.upload-artifact-details')).toContainText('SHA-256')
  await upload.getByRole('button', { name: 'Запустить импорт' }).click()
  const ingestStatus = page.locator('[aria-label="Импорт в версию данных"]')
  await expect(ingestStatus).toContainText('DatasetVersion')
  await expect(ingestStatus).toContainText('ready', { timeout: 90_000 })
  await expect(upload.locator('.upload-version-list li').filter({ hasText: 'roads · v1' }))
    .toContainText('ready')

  const projectResponse = await page.request.get(
    `http://localhost:8000/api/v1/projects/${projectId}/dataset-versions`,
  )
  expect(projectResponse.ok()).toBe(true)
  const versions = await projectResponse.json()
  const versionId = versions.versions[0].id
  expect(versions.versions[0].status).toBe('ready')

  const sourceResponse = await page.request.get(
    `http://localhost:8000/api/v1/projects/${projectId}` +
    `/dataset-versions/${versionId}/source-layers/roads/geojson`,
    { params: { bbox: '37.60,55.74,37.64,55.77', limit: 50 } },
  )
  expect(sourceResponse.status()).toBe(200)
  const source = await sourceResponse.json()
  expect(source.features.map((item) => item.properties.name))
    .toContain('Browser Ingest Road')
  await upload.locator('.upload-version-list li').filter({ hasText: 'roads · v1' })
    .getByRole('button', { name: 'Выбрать' }).click()
  await expect(page.locator('.workspace-context-summary')).toContainText(versionId.slice(0, 8))
})
