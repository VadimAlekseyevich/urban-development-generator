import { useMemo, useState, type FormEvent } from 'react'

import {
  cancelRun,
  createRun,
  retryRun,
  type RunLifecycleStatus,
  type RunState,
} from './runApi'
import { useRunPolling } from './runPolling'
import './runs.css'

type RunsPanelProps = {
  apiBase: string
  projectId: string | null
  datasetVersionId: string | null
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
  apiBase, projectId, datasetVersionId, mapRunId, onMapRunChange,
}: RunsPanelProps) {
  const [refreshKey, setRefreshKey] = useState(0)
  const { runs, truncated, loading, error } = useRunPolling(apiBase, projectId, refreshKey)
  const [selectedRunId, setSelectedRunId] = useState('')
  const [mode, setMode] = useState<RunState['mode']>('EXPANSION')
  const [seed, setSeed] = useState('42')
  const [configVersion, setConfigVersion] = useState('1')
  const [configText, setConfigText] = useState('{}')
  const [commitSha, setCommitSha] = useState(import.meta.env.VITE_GENERATION_COMMIT_SHA ?? '')
  const [busy, setBusy] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const [actionMessage, setActionMessage] = useState<string | null>(null)

  const selected = useMemo(
    () => runs.find((run) => run.id === selectedRunId) ?? runs[0] ?? null,
    [runs, selectedRunId],
  )

  function refresh(): void {
    setRefreshKey((current) => current + 1)
  }

  async function submitCreate(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    if (!projectId || !datasetVersionId || busy) return
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
        dataset_version_ids: [datasetVersionId],
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
          <input value={configVersion} maxLength={64}
            onChange={(event) => setConfigVersion(event.target.value)} required />
        </label>
        <label>
          Исполняемый commit SHA (40 hex)
          <input value={commitSha} pattern="[0-9a-f]{40}" maxLength={40}
            onChange={(event) => setCommitSha(event.target.value)} placeholder="Commit worker-сборки" required />
        </label>
        <label>
          Полная конфигурация (JSON)
          <textarea rows={4} value={configText}
            onChange={(event) => setConfigText(event.target.value)} spellCheck={false} required />
        </label>
        <p className="helper-text">
          Привязка к выбранной DatasetVersion: {datasetVersionId ? shortId(datasetVersionId) : 'не выбрана'}.
          Указывайте реальный исполняемый commit и поддерживаемую worker конфигурацию.
        </p>
        <button className="button button-primary" disabled={!projectId || !datasetVersionId || busy}
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
