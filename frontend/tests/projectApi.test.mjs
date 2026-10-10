import test from 'node:test'
import assert from 'node:assert/strict'
import { createProject, listProjects } from '../src/projectApi.ts'

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
