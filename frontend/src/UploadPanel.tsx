import { useEffect, useRef, useState, type ChangeEvent } from 'react'

import { isUuid } from './sourceLayers'
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

  function chooseFile(event: ChangeEvent<HTMLInputElement>): void {
    setFile(event.target.files?.[0] ?? null)
    setProgress(null)
    setError(null)
    setArtifact(null)
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
      <p className="helper-text">
        Загрузка создаёт только готовый Artifact. Привязка к DatasetVersion и импорт
        выполняются отдельно; новый ID версии не назначается автоматически.
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
