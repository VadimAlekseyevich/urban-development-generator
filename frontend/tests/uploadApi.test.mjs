import test from 'node:test'
import assert from 'node:assert/strict'
import { File } from 'node:buffer'

import {
  fetchDatasetVersions,
  formatUploadSize,
  uploadArtifact,
  UploadRequestError,
} from '../src/uploadApi.ts'

class Emitter {
  listeners = new Map()
  addEventListener(event, callback) {
    const list = this.listeners.get(event) ?? []
    list.push(callback)
    this.listeners.set(event, list)
  }
  fire(event, payload = {}) {
    for (const callback of this.listeners.get(event) ?? []) callback(payload)
  }
}

class FakeXhr extends Emitter {
  upload = new Emitter()
  status = 0
  responseText = ''
  timeout = null
  open(method, url) {
    this.method = method
    this.url = url
  }
  send(body) {
    this.body = body
  }
  abort() {
    this.fire('abort')
  }
  finish(status, payload) {
    this.status = status
    this.responseText = typeof payload === 'string' ? payload : JSON.stringify(payload)
    this.fire('load')
  }
}

const file = new File(['road-data'], 'roads.geojson', { type: 'application/geo+json' })
const ready = {
  artifact_id: '11111111-1111-4111-8111-111111111111',
  key: 'uploads/test/roads.geojson',
  filename: 'roads.geojson',
  state: 'ready',
  size_bytes: 9,
  checksum: 'sha256:' + 'a'.repeat(64),
  content_type: 'application/geo+json',
}

test('multipart sends the original binary File and emits upload/processing phases', async () => {
  const xhr = new FakeXhr()
  const progress = []
  const request = uploadArtifact('http://localhost:8000/api/v1/', file, (value) => {
    progress.push(value)
  }, () => xhr)
  assert.equal(xhr.method, 'POST')
  assert.equal(xhr.url, 'http://localhost:8000/api/v1/uploads')
  assert.equal(xhr.timeout, 0)
  assert.equal(xhr.body.get('file').name, 'roads.geojson')
  assert.equal(await xhr.body.get('file').text(), 'road-data')

  xhr.upload.fire('progress', { loaded: 3, total: 9, lengthComputable: true })
  xhr.upload.fire('load')
  xhr.finish(201, ready)
  assert.deepEqual(await request.promise, ready)
  assert.deepEqual(progress.map((item) => item.phase), ['uploading', 'processing'])
  assert.equal(progress[0].percent, 33)
  assert.equal(progress[1].percent, 100)
})

test('unknown transfer length remains indeterminate', async () => {
  const xhr = new FakeXhr()
  const progress = []
  const request = uploadArtifact('http://localhost/api/v1', file, (value) => {
    progress.push(value)
  }, () => xhr)
  xhr.upload.fire('progress', { loaded: 4, total: 0, lengthComputable: false })
  xhr.finish(201, ready)
  await request.promise
  assert.equal(progress[0].percent, null)
  assert.equal(progress[0].total, null)
})

test('HTTP 413 exposes server limit; no implicit replay after a rejected upload', async () => {
  const xhr = new FakeXhr()
  const request = uploadArtifact('http://localhost/api/v1', file, () => {}, () => xhr)
  xhr.finish(413, { detail: 'Upload exceeds the configured 4 byte limit' })
  await assert.rejects(request.promise, (error) => {
    assert.ok(error instanceof UploadRequestError)
    assert.equal(error.status, 413)
    assert.equal(error.canRetry, false)
    assert.match(error.message, /4 byte limit/)
    return true
  })
})

test('cancel and network failures do not retry automatically', async () => {
  const xhr = new FakeXhr()
  const request = uploadArtifact('http://localhost/api/v1', file, () => {}, () => xhr)
  request.cancel()
  await assert.rejects(request.promise, (error) => {
    assert.equal(error.category, 'cancelled')
    return true
  })

  const failedXhr = new FakeXhr()
  const failed = uploadArtifact('http://localhost/api/v1', file, () => {}, () => failedXhr)
  failedXhr.fire('error')
  await assert.rejects(failed.promise, (error) => {
    assert.equal(error.category, 'network')
    assert.equal(error.canRetry, true)
    return true
  })
})

test('malformed successful API response cannot be mistaken for a stored artifact', async () => {
  const xhr = new FakeXhr()
  const request = uploadArtifact('http://localhost/api/v1', file, () => {}, () => xhr)
  xhr.finish(200, { id: 'missing-artifact-id' })
  await assert.rejects(request.promise, /некорректный ответ/)
})

test('dataset version listing uses a project-owned bounded API, not upload artifact IDs', async () => {
  const nativeFetch = globalThis.fetch
  let calledUrl = ''
  globalThis.fetch = async (url) => {
    calledUrl = String(url)
    return { ok: true, json: async () => ({
      project_id: 'p', limit: 20, offset: 20, truncated: false, versions: [],
    }) }
  }
  try {
    const data = await fetchDatasetVersions(
      'http://localhost:8000/api/v1', 'project id', new AbortController().signal, 20,
    )
    assert.equal(data.offset, 20)
    assert.match(calledUrl, /\/projects\/project%20id\/dataset-versions\?limit=20&offset=20$/)
  } finally {
    globalThis.fetch = nativeFetch
  }
})

test('size labels remain human-readable for very large uploads', () => {
  assert.equal(formatUploadSize(0), '0 Б')
  assert.equal(formatUploadSize(1024), '1.0 КиБ')
  assert.equal(formatUploadSize(1024 ** 3), '1.00 ГиБ')
})
