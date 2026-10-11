import test from 'node:test'
import assert from 'node:assert/strict'
import { fetchMetricsCsv } from '../src/exportApi.ts'

test('CSV export submits bounded ordered run IDs to the canonical endpoint', async () => {
  const previous = globalThis.fetch
  let calledUrl = ''
  let init
  globalThis.fetch = async (url, options) => {
    calledUrl = String(url)
    init = options
    return { ok: true, blob: async () => new Blob(['run_id,metric_id\n']) }
  }
  try {
    const result = await fetchMetricsCsv('http://localhost:8000/api/v1', 'project-id', ['run-a', 'run-b'])
    assert.equal(await result.text(), 'run_id,metric_id\n')
    assert.match(calledUrl, /projects\/project-id\/exports\/metrics\.csv$/)
    assert.equal(init.method, 'POST')
    assert.deepEqual(JSON.parse(init.body), { run_ids: ['run-a', 'run-b'] })
  } finally {
    globalThis.fetch = previous
  }
})

test('CSV export rejects invalid cardinality and duplicate runs before network', async () => {
  await assert.rejects(fetchMetricsCsv('', '', []), /1–10/)
  await assert.rejects(fetchMetricsCsv('', '', ['x', 'x']), /различных/)
  await assert.rejects(fetchMetricsCsv('', '', Array(11).fill('x')), /1–10/)
})

test('CSV export surfaces HTTP errors; it never downloads a false success', async () => {
  const previous = globalThis.fetch
  globalThis.fetch = async () => ({
    ok: false, status: 409, text: async () => '{"detail":"evaluation unavailable"}',
  })
  try {
    await assert.rejects(fetchMetricsCsv('http://localhost/api/v1', 'project', ['run']), /HTTP 409/)
  } finally {
    globalThis.fetch = previous
  }
})
