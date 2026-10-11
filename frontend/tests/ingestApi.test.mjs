import test from 'node:test'
import assert from 'node:assert/strict'

import {
  inferIngestFormat, scheduleIngest, readIngest,
} from '../src/ingestApi.ts'

test('format inference only advertises supported worker formats', () => {
  assert.equal(inferIngestFormat('road.GeoJSON'), 'geojson')
  assert.equal(inferIngestFormat('parcels.GPKG'), 'gpkg')
  assert.equal(inferIngestFormat('roads.ZIP'), 'shapefile_zip')
  assert.equal(inferIngestFormat('raster.TIFF'), 'geotiff')
  assert.equal(inferIngestFormat('city.pbf'), null)
})

test('schedule and poll use project-scoped canonical endpoints', async () => {
  const oldFetch = globalThis.fetch
  const calls = []
  globalThis.fetch = async (url, init) => {
    calls.push([String(url), init])
    return { ok: true, json: async () => ({
      dataset_version_id: 'version', status: 'uploaded', job_status: 'queued',
    }) }
  }
  try {
    const scheduled = await scheduleIngest(
      '/api/v1', 'project', 'artifact', 'roads', 'geojson',
    )
    assert.equal(scheduled.dataset_version_id, 'version')
    const signal = new AbortController().signal
    await readIngest('/api/v1', 'project', 'version', signal)
    assert.equal(calls[0][0], '/api/v1/projects/project/ingests')
    assert.equal(calls[0][1].method, 'POST')
    assert.deepEqual(JSON.parse(calls[0][1].body), {
      artifact_id: 'artifact', dataset_kind: 'roads', source_format: 'geojson',
    })
    assert.equal(calls[1][0], '/api/v1/projects/project/ingests/version')
    assert.equal(calls[1][1].signal, signal)
  } finally {
    globalThis.fetch = oldFetch
  }
})

test('ingest HTTP errors are surfaced, not silently marked ready', async () => {
  const oldFetch = globalThis.fetch
  globalThis.fetch = async () => ({
    ok: false, status: 409, text: async () => '{"detail":"already in use"}',
  })
  try {
    await assert.rejects(
      scheduleIngest('/api/v1', 'p', 'a', 'roads', 'geojson'),
      /HTTP 409/,
    )
  } finally {
    globalThis.fetch = oldFetch
  }
})
