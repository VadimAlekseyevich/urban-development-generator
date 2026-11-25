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
  pinnedRunId: string | null
  onMapRunChange: (runId: string | null) => void
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

const RAW_LABELS: Record<string, string> = {
  'land.developable_area_m2': 'Пригодная для освоения площадь',
  'land.developed_area_m2': 'Освоенная площадь',
  'land.green_recreation_share': 'Доля зелёных и рекреационных зон',
  'buildings.coverage_ratio': 'Доля пятна застройки',
  'buildings.far': 'Коэффициент интенсивности застройки (FAR)',
  'buildings.gfa_m2': 'Суммарная поэтажная площадь',
  'demography.total_population': 'Расчётное население',
  'demography.density_per_km2': 'Плотность населения',
  'demography.jobs_estimate': 'Расчётные рабочие места',
  'roads.length_density_km_per_km2': 'Плотность дорожной сети',
  'roads.connected_components': 'Компоненты дорожной сети',
  'roads.average_degree': 'Средняя степень дорожного узла',
  'roads.intersection_density_per_km2': 'Плотность перекрёстков',
  'roads.circuity': 'Непрямолинейность маршрутов',
  'roads.dead_end_ratio': 'Доля тупиков',
  'infrastructure.population_coverage_ratio': 'Охват населения инфраструктурой',
  'infrastructure.network_distance_p50_m': 'Медианное сетевое расстояние',
  'infrastructure.network_distance_p90_m': '90-й процентиль сетевого расстояния',
  'infrastructure.unmet_demand': 'Неудовлетворённый спрос',
  'infrastructure.capacity_utilization': 'Использование вместимости',
  'constraints.hard_violation_count': 'Число жёстких нарушений',
  'constraints.affected_area_m2': 'Площадь нарушений',
  'constraints.weighted_soft_penalty': 'Штраф за мягкие ограничения',
}
const RATIO_IDS = new Set([
  'land.green_recreation_share', 'buildings.coverage_ratio', 'roads.dead_end_ratio',
  'infrastructure.population_coverage_ratio', 'infrastructure.capacity_utilization',
])

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
  pinnedRunId,
  onMapRunChange,
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
  }, [apiBase, projectId, pinnedRunId])

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

  useEffect(() => {
    if (pinnedRunId && runs.some((run) => run.id === pinnedRunId)) {
      setSelectedRunId(pinnedRunId)
    }
  }, [pinnedRunId, runs])

  function changeRun(event: ChangeEvent<HTMLSelectElement>): void {
    const runId = event.target.value
    setSelectedRunId(runId)
    onMapRunChange(runId || null)
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
          <h2>Показатели района</h2>
        </div>
        <span className="badge">
          {selectedRun ? selectedRun.composite_score.toFixed(3) : '—'}
        </span>
      </div>

      <label className="metrics-select">
        <span>Запуск</span>
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
      {pinnedRunId && !runs.some((run) => run.id === pinnedRunId) && (
        <p className="warning-text">
          Выбранный для карты run отсутствует в ограниченном списке metric runs;
          другой run не подставляется как его метрика.
        </p>
      )}
      {selectedRun && (
        <button className="button" type="button"
          aria-pressed={pinnedRunId === selectedRun.id}
          onClick={() => onMapRunChange(pinnedRunId === selectedRun.id ? null : selectedRun.id)}>
          {pinnedRunId === selectedRun.id ? 'Открепить от карты' : 'Показать метрики этого run на карте'}
        </button>
      )}

      {dashboard && (
        <>
          <div className="metrics-score-summary">
            <div>
              <span>Оценка по выбранным критериям</span>
              <strong>{dashboard.composite_score.toFixed(4)}</strong>
            </div>
            <div>
              <span>Критериев в оценке</span>
              <strong>{dashboard.metrics.length}</strong>
            </div>
          </div>

          {dashboard.metrics.length === 1 &&
            dashboard.metrics[0].metric_id === 'constraints.hard_violation_count' && (
            <p className="warning-text">Оценка учитывает только жёсткие нарушения.
              1,0 не означает, что район качественно спроектирован.</p>
          )}
          <h3>Фактические показатели модели</h3>
          {dashboard.raw_metrics_available ? (
            <dl className="metrics-provenance" aria-label="Фактические показатели модели">
              {(dashboard.raw_metrics ?? []).map(metric => (
                <div key={metric.metric_id}>
                  <dt title={metric.metric_id}>{RAW_LABELS[metric.metric_id] ?? metric.metric_id}</dt>
                  <dd>{RATIO_IDS.has(metric.metric_id) ? formatPercent(metric.raw_value)
                    : formatNumber(metric.raw_value)}
                    {metric.raw_value !== null && !RATIO_IDS.has(metric.metric_id) ? ' ' + metric.unit : ''}
                  </dd>
                </div>
              ))}
            </dl>
          ) : (
            <p className="warning-text">Для этого старого запуска полный набор показателей не
              сохранён. Он доступен для новых запусков после обновления.</p>
          )}
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
          <h3>Состав оценки</h3>
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
