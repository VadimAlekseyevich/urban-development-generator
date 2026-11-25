import test from 'node:test'
import assert from 'node:assert/strict'
import { createProject, listProjects, parseBoundaryGeoJSON, saveProjectBoundary } from '../src/projectApi.ts'

test('project discovery uses bounded paginated existing API', async () => {
  const prev = globalThis.fetch
  let url = ''
  let options
  globalThis.fetch = async (requestUrl, init) => {
    url = String(requestUrl)
    options = init
    return { ok: true, json: async () => [{
      id: 'p', name: 'Demo', working_srid: 32637,
    }] }
  }
  try {
    const signal = new AbortController().signal
    const result = await listProjects('http://localhost/api/v1/', signal, 50)
    assert.equal(result[0].name, 'Demo')
    assert.equal(options.signal, signal)
    assert.equal(url, 'http://localhost/api/v1/projects?limit=50&offset=50')
    assert.throws(() => listProjects('http://localhost/api/v1', signal, -1), /offset/)
  } finally { globalThis.fetch = prev }
})

test('project creation posts JSON to canonical API and returns persisted UUID', async () => {
  const prev = globalThis.fetch
  let url = ''
  let init
  globalThis.fetch = async (requestUrl, options) => {
    url = String(requestUrl)
    init = options
    return { ok: true, json: async () => ({
      id: 'persisted-id', name: 'Urban', working_srid: 32637,
    }) }
  }
  try {
    const created = await createProject('http://localhost/api/v1', {
      name: 'Urban', working_srid: 32637, description: null,
    })
    assert.equal(created.id, 'persisted-id')
    assert.equal(url, 'http://localhost/api/v1/projects')
    assert.equal(init.method, 'POST')
    assert.deepEqual(JSON.parse(init.body), {
      name: 'Urban', working_srid: 32637, description: null,
    })
  } finally { globalThis.fetch = prev }
})

test('project errors are surfaced rather than selecting a phantom project', async () => {
  const prev = globalThis.fetch
  globalThis.fetch = async () => ({
    ok: false, status: 422, text: async () => '{"detail":"invalid SRID"}',
  })
  try {
    await assert.rejects(
      createProject('http://localhost/api/v1', { name: 'A', description: null, working_srid: 4326 }),
      /HTTP 422/,
    )
  } finally { globalThis.fetch = prev }
})

const polygon = { type: 'Polygon', coordinates: [[[37.6, 55.7], [37.61, 55.7], [37.61, 55.71], [37.6, 55.7]]] }

test('boundary accepts a geometry, Feature or single-feature collection without CRS ambiguity', () => {
  const feature = { type: 'Feature', properties: {}, geometry: polygon }
  for (const value of [polygon, feature, { type: 'FeatureCollection', features: [feature] }]) {
    assert.deepEqual(parseBoundaryGeoJSON(JSON.stringify(value)), polygon)
  }
  assert.throws(() => parseBoundaryGeoJSON(JSON.stringify({ ...polygon, crs: {} })), /crs/)
  assert.throws(() => parseBoundaryGeoJSON(JSON.stringify({ type: 'FeatureCollection', features: [feature, feature] })), /ровно один/)
  assert.throws(() => parseBoundaryGeoJSON('{"type":"Point","coordinates":[37,55]}'), /Polygon/)
  assert.throws(() => parseBoundaryGeoJSON('invalid JSON'))
})

test('boundary PUT uses explicit WGS84 and surfaces server-side lock errors', async () => {
  const previous = globalThis.fetch
  const signal = new AbortController().signal
  try {
    globalThis.fetch = async (url, init) => {
      assert.equal(url, 'http://localhost/api/v1/projects/p/boundary')
      assert.equal(init.method, 'PUT')
      assert.equal(init.signal, signal)
      assert.deepEqual(JSON.parse(init.body), { source_srid: 4326, geometry: polygon })
      return { ok: true, json: async () => ({ id: 'p' }) }
    }
    assert.equal((await saveProjectBoundary('http://localhost/api/v1/', 'p', polygon, signal)).id, 'p')
    globalThis.fetch = async () => ({ ok: false, status: 409, text: async () => 'locked' })
    await assert.rejects(saveProjectBoundary('http://localhost/api/v1', 'p', polygon, signal), /HTTP 409/)
  } finally { globalThis.fetch = previous }
})
