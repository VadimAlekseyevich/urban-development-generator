import { useEffect, useMemo, useState, type FormEvent } from 'react'

import {
  cancelRun,
  createRun,
  retryRun,
  type RunLifecycleStatus,
  type RunState,
} from './runApi'
import { useRunPolling } from './runPolling'
import { fetchDatasetVersions, type DatasetVersionInfo, type DatasetVersionPage } from './uploadApi'
import './runs.css'

type RunsPanelProps = {
  apiBase: string
  projectId: string | null
  datasetVersionId: string | null
  active: boolean
  mapRunId: string | null
  onMapRunChange: (runId: string | null) => void
}

const LABEL: Record<RunLifecycleStatus, string> = {
  queued: 'В очереди',
  running: 'Выполняется',
  succeeded: 'Завершён',
  failed: 'Ошибка',
  cancelled: 'Отменён',
}

function dateLabel(value: string | null): string {
  return value ? new Date(value).toLocaleString('ru-RU') : '—'
}

function shortId(value: string): string {
  return value.slice(0, 8)
}

function runOption(run: RunState): string {
  return `${shortId(run.id)} · ${LABEL[run.status]} · seed ${run.seed}`
}

export function RunsPanel({
  apiBase, projectId, datasetVersionId, active, mapRunId, onMapRunChange,
}: RunsPanelProps) {
  const [refreshKey, setRefreshKey] = useState(0)
  const { runs, truncated, loading, error } = useRunPolling(apiBase, projectId, refreshKey)
  const [selectedRunId, setSelectedRunId] = useState('')
  const [mode, setMode] = useState<RunState['mode']>('EXPANSION')
  const [seed, setSeed] = useState('42')
  const configVersion = 'canonical-stage-configs-v5'
  const [configText, setConfigText] = useState('')
  const [commitSha, setCommitSha] = useState(import.meta.env.VITE_GENERATION_COMMIT_SHA ?? '')
  const [busy, setBusy] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const [actionMessage, setActionMessage] = useState<string | null>(null)
  const [inputPage, setInputPage] = useState<DatasetVersionPage | null>(null)
  const [inputOffset, setInputOffset] = useState(0)
  const [inputRefresh, setInputRefresh] = useState(0)
  const [inputLoading, setInputLoading] = useState(false)
  const [inputError, setInputError] = useState<string | null>(null)
  const [chosenInputs, setChosenInputs] = useState<DatasetVersionInfo[]>([])

  useEffect(() => {
    if (!projectId || !active) return
    const controller = new AbortController()
    setInputLoading(true)
    setInputError(null)
    setInputPage(null)
    void fetchDatasetVersions(apiBase, projectId, controller.signal, inputOffset)
      .then((page) => {
        if (!controller.signal.aborted) setInputPage(page)
      })
      .catch((caught: unknown) => {
        if (!controller.signal.aborted) {
          setInputError(caught instanceof Error ? caught.message : String(caught))
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setInputLoading(false)
      })
    return () => controller.abort()
  }, [apiBase, projectId, active, inputOffset, inputRefresh])

  function toggleInput(version: DatasetVersionInfo): void {
    if (version.status !== 'ready') return
    setChosenInputs((current) => current.some((item) => item.id === version.id)
      ? current.filter((item) => item.id !== version.id)
      : current.length < 32 ? [...current, version] : current)
  }

  const selected = useMemo(
    () => runs.find((run) => run.id === selectedRunId) ?? runs[0] ?? null,
    [runs, selectedRunId],
  )

  function refresh(): void {
    setRefreshKey((current) => current + 1)
  }

  async function submitCreate(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    if (!projectId || chosenInputs.length === 0 || busy) return
    setActionError(null)
    setActionMessage(null)
    if (!/^\d+$/.test(seed) || !Number.isSafeInteger(Number(seed))) {
      setActionError('Seed должен быть целым неотрицательным числом.')
      return
    }
    let config: unknown
    try {
      config = JSON.parse(configText) as unknown
    } catch {
      setActionError('Конфигурация должна быть корректным JSON-объектом.')
      return
    }
    if (config === null || Array.isArray(config) || typeof config !== 'object') {
      setActionError('Конфигурация должна быть JSON-объектом.')
      return
    }
    setBusy(true)
    try {
      const run = await createRun(apiBase, projectId, {
        mode,
        seed: Number(seed),
        dataset_version_ids: chosenInputs.map((item) => item.id),
        config_json: config as Record<string, unknown>,
        config_schema_version: configVersion,
        commit_sha: commitSha.trim(),
      })
      setSelectedRunId(run.id)
      setActionMessage(`Запуск ${shortId(run.id)} зарегистрирован в БД и outbox.`)
      refresh()
    } catch (caught: unknown) {
      setActionError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setBusy(false)
    }
  }

  async function act(kind: 'cancel' | 'retry'): Promise<void> {
    if (!projectId || !selected || busy) return
    setBusy(true)
    setActionError(null)
    setActionMessage(null)
    try {
      const result = kind === 'cancel'
        ? await cancelRun(apiBase, projectId, selected.id)
        : await retryRun(apiBase, projectId, selected.id)
      setSelectedRunId(result.id)
      setActionMessage(
        kind === 'cancel'
          ? result.status === 'running'
            ? 'Запрос отмены сохранён. Worker завершит запуск на безопасной границе.'
            : `Текущее состояние: ${LABEL[result.status]}.`
          : `Создан или найден повторный запуск ${shortId(result.id)}; исходный не изменён.`,
      )
      refresh()
    } catch (caught: unknown) {
      setActionError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="panel run-panel" aria-label="Управление генерацией">
      <div className="section-heading">
        <p className="section-kicker">S12 · Generation</p>
        <h2>Запуски и прогресс</h2>
      </div>
      <form className="run-form" onSubmit={(event) => { void submitCreate(event) }}>
        <label>
          Режим
          <select value={mode} onChange={(event) => setMode(event.target.value as RunState['mode'])}>
            <option value="EXPANSION">Расширение города</option>
            <option value="FROM_SCRATCH">С нуля</option>
          </select>
        </label>
        <label>
          Seed
          <input type="number" min="0" max="9223372036854775807" step="1" value={seed}
            onChange={(event) => setSeed(event.target.value)} required />
        </label>
        <label>
          Версия схемы config
          <input value={configVersion} readOnly />
        </label>
        <label>
          Исполняемый commit SHA (40 hex)
          <input value={commitSha} pattern="[0-9a-f]{40}" maxLength={40}
            onChange={(event) => setCommitSha(event.target.value)} placeholder="Commit worker-сборки" required />
        </label>
        <label>
          Полная конфигурация (JSON)
          <textarea rows={4} value={configText} placeholder="Вставьте полную конфигурацию v5"
            onChange={(event) => setConfigText(event.target.value)} spellCheck={false} required />
        </label>
        <fieldset className="run-inputs" disabled={!projectId || busy}>
          <legend>Исходные данные генерации ({chosenInputs.length}/32)</legend>
          <p className="helper-text">
            Выберите готовые версии всех необходимых слоёв. Слой на карте не определяет состав запуска.
          </p>
          <button type="button" className="button" disabled={inputLoading}
            onClick={() => setInputRefresh((value) => value + 1)}>Обновить исходные данные</button>
          {inputLoading && <p role="status" className="helper-text">Загрузка наборов данных…</p>}
          {inputError && <p role="alert" className="form-error">{inputError}</p>}
          {inputPage?.versions.length === 0 && <p className="helper-text">На этой странице нет наборов данных.</p>}
          {inputPage?.versions.map((version) => {
            const checked = chosenInputs.some((item) => item.id === version.id)
            return (
              <label className="run-input-option" key={version.id}>
                <input type="checkbox" checked={checked}
                  disabled={version.status !== 'ready' || (!checked && chosenInputs.length >= 32)}
                  onChange={() => toggleInput(version)} />
                <span>{version.dataset_kind} · v{version.version} · {shortId(version.id)} · {version.status}
                  {version.id === datasetVersionId ? ' · на карте' : ''}</span>
              </label>
            )
          })}
          <div className="upload-version-nav">
            <button type="button" className="button" disabled={inputLoading || inputOffset === 0}
              onClick={() => setInputOffset((value) => Math.max(0, value - 20))}>Назад</button>
            <span>Страница {Math.floor(inputOffset / 20) + 1}</span>
            <button type="button" className="button" disabled={inputLoading || !inputPage?.truncated}
              onClick={() => setInputOffset((value) => value + 20)}>Далее</button>
          </div>
          {chosenInputs.length > 0 && <ul className="run-input-selected">
            {chosenInputs.map((version) => <li key={version.id}>
              <span>{version.dataset_kind} · v{version.version} · {shortId(version.id)}</span>
              <button type="button" className="button" aria-label={`Удалить ${version.id}`}
                onClick={() => toggleInput(version)}>Убрать</button>
            </li>)}
          </ul>}
        </fieldset>
        <p className="helper-text">Указывайте реальный исполняемый commit и поддерживаемую worker конфигурацию.</p>
        <button className="button button-primary" disabled={!projectId || chosenInputs.length === 0 || busy}
          type="submit">Создать запуск</button>
      </form>

      <div className="run-list-header">
        <strong>Запуски проекта</strong>
        <button className="button" type="button" onClick={refresh} disabled={!projectId || busy}>
          Обновить
        </button>
      </div>
      {loading && <p className="helper-text" role="status">Загружаю состояния из PostgreSQL…</p>}
      {error && <p className="form-error" role="alert">{error}</p>}
      {truncated && <p className="warning-text">Показаны 50 последних записей. Список ограничен.</p>}
      <label className="run-selector">
        Выбранный запуск
        <select value={selected?.id ?? ''} disabled={runs.length === 0}
          onChange={(event) => setSelectedRunId(event.target.value)}>
          {runs.length === 0 && <option value="">Нет запусков</option>}
          {runs.map((run) => <option value={run.id} key={run.id}>{runOption(run)}</option>)}
        </select>
      </label>

      {selected && (
        <div className="run-detail" aria-live="polite">
          <div className="run-state-heading">
            <strong className={`run-status run-status-${selected.status}`}>{LABEL[selected.status]}</strong>
            <code title={selected.id}>{shortId(selected.id)}</code>
          </div>
          <p className="helper-text">
            {selected.mode} · seed {selected.seed} · создан {dateLabel(selected.created_at)}
          </p>
          {selected.rerun_source_id && <p className="helper-text">
            Повтор исходного запуска: {shortId(selected.rerun_source_id)}
          </p>}
          <p className="helper-text">
            Job: {selected.job ? `${LABEL[selected.job.status]} · попыток ${selected.job.attempt_count}/${selected.job.max_attempts}` : 'нет записи'}
          </p>
          {selected.job?.cancel_requested_at && <p className="warning-text">
            Отмена запрошена: {dateLabel(selected.job.cancel_requested_at)}
          </p>}
          {selected.error_message && <p className="form-error">{selected.error_message}</p>}
          {selected.job?.error_code && <p className="form-error">
            {selected.job.error_class}: {selected.job.error_code}
          </p>}
          <div className="run-actions">
            <button className="button" type="button"
              disabled={selected.status !== 'succeeded'}
              aria-pressed={mapRunId === selected.id}
              onClick={() => onMapRunChange(mapRunId === selected.id ? null : selected.id)}>
              {mapRunId === selected.id ? 'Открепить от карты' : 'Показать на карте'}
            </button>
            <button className="button" type="button" disabled={busy || !selected.job
              || !['queued', 'running'].includes(selected.status)
              || selected.job.cancel_requested_at !== null}
              onClick={() => { void act('cancel') }}>Отменить</button>
            <button className="button" type="button" disabled={busy
              || !['failed', 'cancelled'].includes(selected.status)}
              onClick={() => { void act('retry') }}>Повторить</button>
          </div>
          <p className="section-kicker">Авторитетные этапы · {selected.stages.length}</p>
          {selected.stages.length === 0 && <p className="helper-text">
            Worker ещё не опубликовал прогресс этапов.
          </p>}
          <ol className="run-stage-list">
            {selected.stages.map((stage) => (
              <li key={stage.stage_name}>
                <span><strong>{stage.stage_name}</strong><small>{stage.status} · v{stage.stage_version}</small></span>
                <progress max={100} value={stage.progress_percent}
                  aria-label={`${stage.stage_name}: ${stage.progress_percent}%`} />
                <small>{stage.progress_percent}%</small>
              </li>
            ))}
          </ol>
        </div>
      )}
      {actionError && <p className="form-error" role="alert">{actionError}</p>}
      {actionMessage && <p className="load-state load-state-ready" role="status">{actionMessage}</p>}
      <p className="helper-text">
        Опрос раз в 3 секунды только пока есть активные запуски; запросы не перекрываются,
        ошибки повторяются не более трёх раз, при смене проекта запрос отменяется.
      </p>
    </section>
  )
}
