import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
} from 'react'

import {
  fetchMetricDashboard,
  fetchMetricRuns,
  type MetricDashboardMetric,
  type MetricDashboardResponse,
  type MetricRunSummary,
} from './metricsApi'

type LoadStatus = 'idle' | 'loading' | 'ready' | 'error'

type MetricsDashboardProps = {
  apiBase: string
  projectId: string | null
}

const NUMBER_FORMAT = new Intl.NumberFormat('ru-RU', {
  maximumFractionDigits: 4,
})

function formatNumber(value: number | null): string {
  return value === null ? '—' : NUMBER_FORMAT.format(value)
}

function formatPercent(value: number | null): string {
  return value === null ? '—' : `${(value * 100).toFixed(1)}%`
}

function formatRun(run: MetricRunSummary): string {
  return (
    `${run.id.slice(0, 8)} · ${run.status} · ` +
    `score ${run.composite_score.toFixed(3)}`
  )
}

function directionLabel(direction: MetricDashboardMetric['direction']): string {
  switch (direction) {
    case 'HIGHER_IS_BETTER':
      return 'higher is better'
    case 'LOWER_IS_BETTER':
      return 'lower is better'
    case 'TARGET':
      return 'target range'
    case 'DESCRIPTIVE':
      return 'descriptive'
  }
}

function MetricRow({ metric }: { metric: MetricDashboardMetric }) {
  return (
    <article className="metric-card">
      <div className="metric-card-heading">
        <div>
          <strong>{metric.metric_id}</strong>
          <span>
            {metric.scope} · {directionLabel(metric.direction)}
          </span>
        </div>
        <span className="metric-unit">{metric.unit}</span>
      </div>

      <div className="metric-values">
        <div>
          <span>Raw</span>
          <strong>{formatNumber(metric.raw_value)}</strong>
        </div>
        <div>
          <span>Normalized</span>
          <strong>{formatNumber(metric.normalized_value)}</strong>
        </div>
        <div>
          <span>Weight</span>
          <strong>{formatPercent(metric.normalized_weight)}</strong>
          <small>configured {formatNumber(metric.configured_weight)}</small>
        </div>
        <div>
          <span>Contribution</span>
          <strong>{formatNumber(metric.contribution)}</strong>
        </div>
      </div>

      <div className="metric-contribution-track" aria-hidden="true">
        <span
          style={{
            width: `${Math.max(0, Math.min(1, metric.contribution)) * 100}%`,
          }}
        />
      </div>

      <div className="metric-explanation">
        <span>metric v{metric.metric_version}</span>
        <span>normalization v{metric.normalization_policy_version}</span>
        {metric.was_clamped && <span className="metric-flag">clamped</span>}
        {metric.was_missing && <span className="metric-flag">missing input</span>}
      </div>
    </article>
  )
}

export function MetricsDashboard({
  apiBase,
  projectId,
}: MetricsDashboardProps) {
  const runsAbortRef = useRef<AbortController | null>(null)
  const dashboardAbortRef = useRef<AbortController | null>(null)
  const [runs, setRuns] = useState<MetricRunSummary[]>([])
  const [runsTruncated, setRunsTruncated] = useState(false)
  const [selectedRunId, setSelectedRunId] = useState('')
  const [dashboard, setDashboard] = useState<MetricDashboardResponse | null>(null)
  const [loadStatus, setLoadStatus] = useState<LoadStatus>('idle')
  const [loadMessage, setLoadMessage] = useState(
    'Выберите проект с persisted evaluation metrics.',
  )

  const selectedRun = useMemo(
    () => runs.find((run) => run.id === selectedRunId) ?? null,
    [runs, selectedRunId],
  )

  useEffect(() => {
    runsAbortRef.current?.abort()
    dashboardAbortRef.current?.abort()
    setRuns([])
    setRunsTruncated(false)
    setSelectedRunId('')
    setDashboard(null)

    if (!projectId) {
      setLoadStatus('idle')
      setLoadMessage('Выберите проект с persisted evaluation metrics.')
      return
    }

    const controller = new AbortController()
    runsAbortRef.current = controller
    setLoadStatus('loading')
    setLoadMessage('Загружаю runs с persisted score…')

    void fetchMetricRuns(apiBase, projectId, controller.signal)
      .then((response) => {
        if (controller.signal.aborted) return
        setRuns(response.runs)
        setRunsTruncated(response.truncated)

        const requested =
          new URLSearchParams(window.location.search).get('metrics_run_id') ?? ''
        const next =
          response.runs.find((run) => run.id === requested)?.id ??
          response.runs[0]?.id ??
          ''
        setSelectedRunId(next)
        setLoadStatus('ready')
        setLoadMessage(
          response.runs.length
            ? `Evaluation runs: ${response.runs.length}.`
            : 'У проекта пока нет persisted evaluation envelope.',
        )
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return
        setLoadStatus('error')
        setLoadMessage(error instanceof Error ? error.message : String(error))
      })

    return () => controller.abort()
  }, [apiBase, projectId])

  useEffect(() => {
    dashboardAbortRef.current?.abort()
    setDashboard(null)
    if (!projectId || !selectedRunId) return

    const controller = new AbortController()
    dashboardAbortRef.current = controller
    setLoadStatus('loading')
    setLoadMessage('Загружаю persisted metric explanation…')

    void fetchMetricDashboard(
      apiBase,
      projectId,
      selectedRunId,
      controller.signal,
    )
      .then((response) => {
        if (controller.signal.aborted) return
        setDashboard(response)
        setLoadStatus('ready')
        setLoadMessage(
          `Score ${response.composite_score.toFixed(3)} · ` +
            `${response.metrics.length} score metrics.`,
        )
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return
        setLoadStatus('error')
        setLoadMessage(error instanceof Error ? error.message : String(error))
      })

    return () => controller.abort()
  }, [apiBase, projectId, selectedRunId])

  function changeRun(event: ChangeEvent<HTMLSelectElement>): void {
    const runId = event.target.value
    setSelectedRunId(runId)
    const url = new URL(window.location.href)
    if (runId) url.searchParams.set('metrics_run_id', runId)
    else url.searchParams.delete('metrics_run_id')
    window.history.replaceState({}, '', url)
  }

  return (
    <section className="panel metrics-dashboard">
      <div className="section-heading section-heading-row">
        <div>
          <p className="section-kicker">S11 · Evaluation</p>
          <h2>Metrics dashboard</h2>
        </div>
        <span className="badge">
          {selectedRun ? selectedRun.composite_score.toFixed(3) : '—'}
        </span>
      </div>

      <label className="metrics-select">
        <span>Metric run</span>
        <select
          value={selectedRunId}
          onChange={changeRun}
          disabled={!projectId || runs.length === 0}
        >
          {runs.length === 0 && (
            <option value="">Нет persisted evaluation runs</option>
          )}
          {runs.map((run) => (
            <option value={run.id} key={run.id}>
              {formatRun(run)}
            </option>
          ))}
        </select>
      </label>

      {dashboard && (
        <>
          <div className="metrics-score-summary">
            <div>
              <span>Composite score</span>
              <strong>{dashboard.composite_score.toFixed(4)}</strong>
            </div>
            <div>
              <span>Metrics</span>
              <strong>{dashboard.metrics.length}</strong>
            </div>
          </div>

          <dl className="metrics-provenance">
            <div>
              <dt>Score config</dt>
              <dd>
                {dashboard.score_config_id} · v{dashboard.score_config_version}
              </dd>
            </div>
            <div>
              <dt>Normalization</dt>
              <dd>
                {dashboard.normalization_profile_id} · v
                {dashboard.normalization_profile_version}
              </dd>
            </div>
            <div>
              <dt>Run</dt>
              <dd>
                {dashboard.mode} · seed {dashboard.seed} · EPSG:
                {dashboard.working_srid}
              </dd>
            </div>
          </dl>
        </>
      )}

      <div className={`load-state load-state-${loadStatus}`}>
        {loadMessage}
      </div>
      {runsTruncated && (
        <p className="warning-text">
          Показаны последние 100 runs с evaluation envelope.
        </p>
      )}

      {dashboard && (
        <div className="metrics-list" aria-label="Score metric explanation">
          {dashboard.metrics.map((metric) => (
            <MetricRow metric={metric} key={metric.metric_id} />
          ))}
        </div>
      )}

      <p className="helper-text">
        Значения читаются из persisted evaluation envelope. Dashboard не
        пересчитывает GIS, normalization или composite score.
      </p>
    </section>
  )
}
