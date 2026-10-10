/** S13 upload transport: stream the File as multipart bytes, never base64. */

export type UploadedArtifact = {
  artifact_id: string
  key: string
  filename: string
  state: 'ready'
  size_bytes: number
  checksum: string
  content_type: string | null
}

export type DatasetVersionInfo = {
  id: string
  dataset_id: string
  dataset_kind: string
  version: number
  status: string
  checksum_sha256: string | null
  created_at: string
}

export type DatasetVersionPage = {
  project_id: string
  limit: number
  offset: number
  truncated: boolean
  versions: DatasetVersionInfo[]
}

export type UploadProgress = {
  loaded: number
  total: number | null
  percent: number | null
  phase: 'uploading' | 'processing'
}

export class UploadRequestError extends Error {
  readonly category: 'http' | 'network' | 'cancelled' | 'response'
  readonly status: number | null

  constructor(
    message: string,
    category: 'http' | 'network' | 'cancelled' | 'response',
    status: number | null = null,
  ) {
    super(message)
    this.name = 'UploadRequestError'
    this.category = category
    this.status = status
  }

  get canRetry(): boolean {
    return this.category === 'network' || (
      this.category === 'http' &&
      (this.status === 408 || this.status === 429 || (this.status ?? 0) >= 500)
    )
  }
}

function errorDetail(raw: string, status: number): string {
  try {
    const payload: unknown = JSON.parse(raw)
    if (payload && typeof payload === 'object' && 'detail' in payload) {
      const detail = (payload as { detail: unknown }).detail
      if (typeof detail === 'string') return detail.slice(0, 320)
    }
  } catch {
    // Non-JSON responses are possible at proxies.
  }
  return 'HTTP ' + status + (raw ? ': ' + raw.slice(0, 160) : '')
}

function isArtifact(value: unknown): value is UploadedArtifact {
  if (!value || typeof value !== 'object') return false
  const item = value as Partial<UploadedArtifact>
  return typeof item.artifact_id === 'string' &&
    typeof item.filename === 'string' &&
    typeof item.key === 'string' &&
    item.state === 'ready' &&
    typeof item.size_bytes === 'number' &&
    typeof item.checksum === 'string'
}

export type UploadOperation = {
  promise: Promise<UploadedArtifact>
  cancel: () => void
}

export function uploadArtifact(
  apiBase: string,
  file: File,
  onProgress: (progress: UploadProgress) => void,
  makeXhr: () => XMLHttpRequest = () => new XMLHttpRequest(),
): UploadOperation {
  const xhr = makeXhr()
  const form = new FormData()
  form.append('file', file, file.name)

  const promise = new Promise<UploadedArtifact>((resolve, reject) => {
    xhr.upload.addEventListener('progress', (event: ProgressEvent) => {
      onProgress({
        loaded: event.loaded,
        total: event.lengthComputable ? event.total : null,
        percent: event.lengthComputable && event.total > 0
          ? Math.min(100, Math.round((100 * event.loaded) / event.total))
          : null,
        phase: 'uploading',
      })
    })
    xhr.upload.addEventListener('load', () => {
      onProgress({ loaded: file.size, total: file.size, percent: 100, phase: 'processing' })
    })
    xhr.addEventListener('load', () => {
      if (xhr.status < 200 || xhr.status >= 300) {
        reject(new UploadRequestError(
          errorDetail(xhr.responseText, xhr.status), 'http', xhr.status,
        ))
        return
      }
      try {
        const value: unknown = JSON.parse(xhr.responseText)
        if (!isArtifact(value)) {
          throw new Error('missing artifact metadata')
        }
        resolve(value)
      } catch {
        reject(new UploadRequestError('Сервер вернул некорректный ответ на загрузку.', 'response'))
      }
    })
    xhr.addEventListener('error', () => reject(new UploadRequestError(
      'Сетевая ошибка при загрузке файла.', 'network',
    )))
    xhr.addEventListener('timeout', () => reject(new UploadRequestError(
      'Истекло время ожидания соединения.', 'network',
    )))
    xhr.addEventListener('abort', () => reject(new UploadRequestError(
      'Загрузка отменена.', 'cancelled',
    )))
    xhr.open('POST', apiBase.replace(/\/$/, '') + '/uploads')
    // The response may take longer than the transfer while checksum and metadata are persisted.
    xhr.timeout = 0
    xhr.send(form)
  })

  return { promise, cancel: () => xhr.abort() }
}

export async function fetchDatasetVersions(
  apiBase: string,
  projectId: string,
  signal: AbortSignal,
  offset = 0,
): Promise<DatasetVersionPage> {
  const url = apiBase.replace(/\/$/, '') + '/projects/' +
    encodeURIComponent(projectId) + '/dataset-versions?limit=20&offset=' + offset
  const response = await fetch(url, { signal })
  if (!response.ok) {
    const raw = await response.text()
    throw new Error(errorDetail(raw, response.status))
  }
  return (await response.json()) as DatasetVersionPage
}

export function formatUploadSize(size: number): string {
  if (!Number.isFinite(size) || size < 0) return '—'
  if (size < 1024) return size + ' Б'
  if (size < 1024 * 1024) return (size / 1024).toFixed(1) + ' КиБ'
  if (size < 1024 ** 3) return (size / (1024 ** 2)).toFixed(1) + ' МиБ'
  return (size / (1024 ** 3)).toFixed(2) + ' ГиБ'
}
