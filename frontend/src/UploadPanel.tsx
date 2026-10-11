import { useEffect, useRef, useState, type ChangeEvent } from 'react'

import { isUuid } from './sourceLayers'
import {
  inferIngestFormat, readIngest, scheduleIngest,
  type DatasetKind, type IngestState,
} from './ingestApi'
import './upload.css'
import {
  fetchDatasetVersions,
  formatUploadSize,
  uploadArtifact,
  UploadRequestError,
  type DatasetVersionPage,
  type UploadOperation,
  type UploadProgress,
  type UploadedArtifact,
} from './uploadApi'

type UploadState = 'idle' | 'uploading' | 'processing' | 'success' | 'error' | 'cancelled'

type Props = {
  apiBase: string
  projectId: string | null
  onVersionSelect: (versionId: string) => void
  selectedVersionId?: string | null
}

export function UploadPanel({ apiBase, projectId, onVersionSelect, selectedVersionId }: Props) {
  const [file, setFile] = useState<File | null>(null)
  const [state, setState] = useState<UploadState>('idle')
  const [progress, setProgress] = useState<UploadProgress | null>(null)
  const [artifact, setArtifact] = useState<UploadedArtifact | null>(null)
  const [datasetKind, setDatasetKind] = useState<DatasetKind>('roads')
  const [ingest, setIngest] = useState<IngestState | null>(null)
  const [ingestBusy, setIngestBusy] = useState(false)
  const [ingestError, setIngestError] = useState<string | null>(null)
  const [error, setError] = useState<UploadRequestError | null>(null)
  const [versionPage, setVersionPage] = useState<DatasetVersionPage | null>(null)
  const [versionError, setVersionError] = useState<string | null>(null)
  const [versionLoading, setVersionLoading] = useState(false)
  const [versionOffset, setVersionOffset] = useState(0)
  const [versionRefresh, setVersionRefresh] = useState(0)
  const requestRef = useRef<UploadOperation | null>(null)
  const serialRef = useRef(0)
  const active = state === 'uploading' || state === 'processing'
  const validProjectId = projectId && isUuid(projectId) ? projectId : null

  useEffect(() => () => {
    serialRef.current += 1
    requestRef.current?.cancel()
  }, [])

  useEffect(() => {
    setVersionOffset(0)
  }, [validProjectId])

  useEffect(() => {
    if (!validProjectId) {
      setVersionPage(null)
      setVersionError(null)
      setVersionLoading(false)
      return
    }
    const controller = new AbortController()
    setVersionLoading(true)
    setVersionError(null)
    void fetchDatasetVersions(apiBase, validProjectId, controller.signal, versionOffset)
      .then((result) => {
        if (!controller.signal.aborted) setVersionPage(result)
      })
      .catch((failure: unknown) => {
        if (controller.signal.aborted) return
        setVersionPage(null)
        setVersionError(failure instanceof Error ? failure.message : String(failure))
      })
      .finally(() => {
        if (!controller.signal.aborted) setVersionLoading(false)
      })
    return () => controller.abort()
  }, [apiBase, validProjectId, versionOffset, versionRefresh])

  useEffect(() => {
    if (!ingest || !validProjectId || ['ready', 'failed'].includes(ingest.status)) return
    const controller = new AbortController()
    const timer = window.setInterval(() => {
      void readIngest(apiBase, validProjectId, ingest.dataset_version_id, controller.signal)
        .then((current) => {
          if (controller.signal.aborted) return
          setIngest(current)
          if (['ready', 'failed'].includes(current.status)) {
            setVersionRefresh((value) => value + 1)
          }
        })
        .catch((caught: unknown) => {
          if (!controller.signal.aborted) {
            setIngestError(caught instanceof Error ? caught.message : String(caught))
          }
        })
    }, 1500)
    return () => {
      controller.abort()
      window.clearInterval(timer)
    }
  }, [apiBase, validProjectId, ingest?.dataset_version_id, ingest?.status])

  async function submitIngest(): Promise<void> {
    if (!artifact || !validProjectId || ingestBusy) return
    const format = inferIngestFormat(artifact.filename)
    if (!format) {
      setIngestError('Формат файла не поддерживается этим worker. PBF импортируется отдельно.')
      return
    }
    setIngestBusy(true)
    setIngestError(null)
    try {
      const scheduled = await scheduleIngest(
        apiBase, validProjectId, artifact.artifact_id, datasetKind, format,
      )
      setIngest(scheduled)
      setVersionRefresh((value) => value + 1)
    } catch (caught: unknown) {
      setIngestError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setIngestBusy(false)
    }
  }

  function chooseFile(event: ChangeEvent<HTMLInputElement>): void {
    setFile(event.target.files?.[0] ?? null)
    setProgress(null)
    setError(null)
    setArtifact(null)
    setIngest(null)
    setIngestError(null)
    setState('idle')
  }

  function startUpload(): void {
    if (!file || active) return
    const serial = ++serialRef.current
    setState('uploading')
    setProgress(null)
    setError(null)
    setArtifact(null)
    const request = uploadArtifact(apiBase, file, (value) => {
      if (serial !== serialRef.current) return
      setProgress(value)
      setState(value.phase)
    })
    requestRef.current = request
    void request.promise
      .then((value) => {
        if (serial !== serialRef.current) return
        setArtifact(value)
        setState('success')
      })
      .catch((failure: unknown) => {
        if (serial !== serialRef.current) return
        const requestError = failure instanceof UploadRequestError
          ? failure
          : new UploadRequestError('Неожиданная ошибка загрузки.', 'network')
        setError(requestError)
        setState(requestError.category === 'cancelled' ? 'cancelled' : 'error')
      })
      .finally(() => {
        if (serial === serialRef.current) requestRef.current = null
      })
  }

  const stateMessage: Record<UploadState, string> = {
    idle: 'Выберите файл для загрузки.',
    uploading: progress?.percent === null || progress === null
      ? 'Отправка файла…' : 'Отправка: ' + progress.percent + '%',
    processing: 'Файл передан, сервер сохраняет и проверяет артефакт…',
    success: 'Артефакт сохранён. Он ещё не является версией набора данных.',
    error: error?.message ?? 'Не удалось загрузить файл.',
    cancelled: 'Загрузка отменена.',
  }

  return (
    <section className="panel" aria-label="Загрузка данных">
      <div className="section-heading">
        <p className="section-kicker">Import · S13</p>
        <h2>Загрузка геоданных</h2>
      </div>
      <div className="upload-controls">
        <label className="upload-file-label" htmlFor="source-upload-file">
          Файл GeoJSON, ZIP/Shapefile, GeoPackage, GeoTIFF или PBF
        </label>
        <input
          id="source-upload-file"
          type="file"
          accept=".geojson,.json,.zip,.shp,.gpkg,.tif,.tiff,.pbf"
          disabled={active}
          onChange={chooseFile}
        />
        {file && <p className="helper-text">{file.name} · {formatUploadSize(file.size)}</p>}
        <div
          className={'load-state load-state-' + (state === 'error' ? 'error' : state === 'success' ? 'ready' : 'loading')}
          role="status"
          aria-live="polite"
        >
          {stateMessage[state]}
        </div>
        {active && (
          <progress
            aria-label="Прогресс отправки файла"
            className="upload-progress"
            max={100}
            value={progress?.percent ?? undefined}
          />
        )}
        <div className="upload-button-row">
          {active ? (
            <button className="button" type="button" onClick={() => requestRef.current?.cancel()}>
              Отменить
            </button>
          ) : (
            <button className="button button-primary" type="button" disabled={!file} onClick={startUpload}>
              {state === 'error' || state === 'cancelled' ? 'Повторить загрузку' : 'Загрузить файл'}
            </button>
          )}
        </div>
        {state === 'error' && (
          <p className="warning-text">
            {error?.canRetry
              ? 'Повтор выполняется только вручную: после сетевого сбоя неизвестно, успел ли сервер сохранить первую копию.'
              : 'Проверьте файл и ограничения сервера перед повтором.'}
          </p>
        )}
        {artifact && (
          <dl className="upload-artifact-details">
            <div><dt>Artifact ID</dt><dd>{artifact.artifact_id}</dd></div>
            <div><dt>Файл</dt><dd>{artifact.filename}</dd></div>
            <div><dt>Размер</dt><dd>{formatUploadSize(artifact.size_bytes)}</dd></div>
            <div><dt>SHA-256</dt><dd title={artifact.checksum}>{artifact.checksum}</dd></div>
          </dl>
        )}
      </div>
      <div className="upload-controls" aria-label="Импорт в версию данных">
        <h3>Импортировать артефакт в проект</h3>
        <label>
          Тип данных
          <select value={datasetKind} onChange={(event) => {
            setDatasetKind(event.target.value as DatasetKind)
          }}>
            <option value="roads">Дороги</option>
            <option value="buildings">Здания</option>
            <option value="landuse">Землепользование</option>
            <option value="water">Водоёмы</option>
            <option value="facilities">Объекты инфраструктуры</option>
            <option value="constraints">Ограничения</option>
            <option value="dem">DEM (GeoTIFF)</option>
          </select>
        </label>
        <button className="button button-primary" type="button"
          disabled={!validProjectId || !artifact || ingestBusy || Boolean(ingest)}
          onClick={() => { void submitIngest() }}>
          {ingestBusy ? 'Постановка в очередь…' : 'Запустить импорт'}
        </button>
        {!validProjectId && <p className="helper-text">
          Сначала выберите проект для импорта.
        </p>}
        {ingest && <p role="status" className="helper-text">
          DatasetVersion {ingest.dataset_version_id.slice(0, 8)}:
          {ingest.status} · Job {ingest.job_status}.
          {ingest.status === 'ready' ? ' Версия готова к выбору на карте.' : ''}
        </p>}
        {ingestError && <p role="alert" className="form-error">{ingestError}</p>}
      </div>
      <p className="helper-text">
        Загрузка сохраняет исходный Artifact. Кнопка импорта создаёт отдельную
        DatasetVersion и Job; только worker после проверки геоданных переводит
        версию в статус ready. Поддерживаются GeoJSON, GeoPackage, ZIP/Shapefile
        и GeoTIFF; PBF пока импортируется отдельным инструментом.
      </p>
      <div className="upload-versions-heading">
        <strong>Версии данных проекта</strong>
        <button
          type="button"
          className="button"
          disabled={!validProjectId || versionLoading}
          onClick={() => setVersionRefresh((current) => current + 1)}
        >
          Обновить
        </button>
      </div>
      {!validProjectId && <p className="helper-text">Укажите корректный Project ID выше для просмотра версий.</p>}
      {versionLoading && <p className="helper-text" role="status">Чтение версий…</p>}
      {versionError && <p className="form-error" role="alert">{versionError}</p>}
      {versionPage && (
        <>
          {versionPage.versions.length === 0 && (
            <p className="helper-text">Для этого проекта версий в выбранном диапазоне нет.</p>
          )}
          <ul className="upload-version-list">
            {versionPage.versions.map((version) => (
              <li key={version.id}>
                <div>
                  <strong>{version.dataset_kind} · v{version.version}</strong>
                  <span>{version.status} · {version.id.slice(0, 8)}</span>
                </div>
                <button
                  type="button"
                  className="button"
                  disabled={version.status !== 'ready'}
                  aria-pressed={selectedVersionId === version.id}
                  onClick={() => onVersionSelect(version.id)}
                  title={version.status === 'ready'
                    ? 'Открыть готовую DatasetVersion на карте'
                    : 'Версия ещё не готова к чтению'}
                >
                  Выбрать
                </button>
              </li>
            ))}
          </ul>
          <div className="upload-version-nav">
            <button className="button" disabled={versionLoading || versionOffset === 0}
              onClick={() => setVersionOffset(Math.max(0, versionOffset - 20))}>Назад</button>
            <span>{versionOffset + 1}–{versionOffset + versionPage.versions.length}</span>
            <button className="button" disabled={versionLoading || !versionPage.truncated}
              onClick={() => setVersionOffset(versionOffset + 20)}>Далее</button>
          </div>
        </>
      )}
    </section>
  )
}
