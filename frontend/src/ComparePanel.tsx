import { useEffect, useRef, useState } from 'react'

import { fetchRunComparison, type RunComparison } from './compareApi'
import {
  MAX_COMPARE_RUNS,
  MIN_COMPARE_RUNS,
  moveBaselineFirst,
  toggleComparedRun,
} from './compareSelection'
import { fetchMetricRuns, type MetricRunSummary } from './metricsApi'
import './compare.css'

type Props = {
  apiBase: string
  projectId: string | null
  mapRunId: string | null
  onMapRunChange: (runId: string | null) => void
}

const NUMBERS = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 4 })

function numberLabel(value: number | null): string {
  return value === null ? '—' : NUMBERS.format(value)
}

function deltaLabel(value: number | null): string {
  if (value === null) return '—'
  return value > 0 ? `+${NUMBERS.format(value)}` : NUMBERS.format(value)
}

function runLabel(runId: string): string {
  return runId.slice(0, 8)
}

export function ComparePanel({
  apiBase,
  projectId,
  mapRunId,
  onMapRunChange,
}: Props) {
  const listAbortRef = useRef<AbortController | null>(null)
  const compareAbortRef = useRef<AbortController | null>(null)
  const [runs, setRuns] = useState<MetricRunSummary[]>([])
  const [truncated, setTruncated] = useState(false)
  const [selectedIds, setSelectedIds] = useState<string[]>([])
  const [comparison, setComparison] = useState<RunComparison | null>(null)
  const [listLoading, setListLoading] = useState(false)
  const [comparing, setComparing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [refreshKey, setRefreshKey] = useState(0)

  useEffect(() => {
    listAbortRef.current?.abort()
    compareAbortRef.current?.abort()
    setRuns([])
    setTruncated(false)
    setSelectedIds([])
    setComparison(null)
    setError(null)
    setComparing(false)
    if (!projectId) {
      setListLoading(false)
      return
    }

    const controller = new AbortController()
    listAbortRef.current = controller
    setListLoading(true)
    void fetchMetricRuns(apiBase, projectId, controller.signal)
      .then((response) => {
        if (controller.signal.aborted) return
        setRuns(response.runs.filter((run) => run.status === 'succeeded'))
        setTruncated(response.truncated)
        setListLoading(false)
      })
      .catch((caught: unknown) => {
        if (controller.signal.aborted) return
        setListLoading(false)
        setError(caught instanceof Error ? caught.message : String(caught))
      })

    return () => {
      controller.abort()
      compareAbortRef.current?.abort()
    }
  }, [apiBase, projectId, refreshKey])

  function invalidateComparison(): void {
    compareAbortRef.current?.abort()
    setComparison(null)
    setComparing(false)
    setError(null)
    onMapRunChange(null)
  }

  function toggleRun(runId: string, checked: boolean): void {
    setSelectedIds((current) => toggleComparedRun(current, runId, checked))
    invalidateComparison()
  }

  function changeBaseline(runId: string): void {
    setSelectedIds((current) => moveBaselineFirst(current, runId))
    invalidateComparison()
  }

  async function compare(): Promise<void> {
    if (!projectId || comparing || selectedIds.length < MIN_COMPARE_RUNS ||
      selectedIds.length > MAX_COMPARE_RUNS) return
    compareAbortRef.current?.abort()
    const controller = new AbortController()
    compareAbortRef.current = controller
    setComparing(true)
    setComparison(null)
    setError(null)
    onMapRunChange(null)
    try {
      const response = await fetchRunComparison(
        apiBase, projectId, selectedIds, controller.signal,
      )
      if (controller.signal.aborted) return
      setComparison(response)
      onMapRunChange(response.baseline_run_id)
    } catch (caught: unknown) {
      if (controller.signal.aborted) return
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      if (!controller.signal.aborted) setComparing(false)
    }
  }

  return (
    <section className="panel compare-panel" aria-label="Сравнение сценариев">
      <div className="section-heading section-heading-row">
        <div>
          <p className="section-kicker">S12 · Compare</p>
          <h2>Сравнить сценарии</h2>
        </div>
        <span className="badge">{selectedIds.length}/{MAX_COMPARE_RUNS}</span>
      </div>
      <p className="helper-text">
        Выберите 2–10 успешных запусков с сохранёнными метриками.
        Первый в списке — базовый; расчёт не запускает GIS повторно.
      </p>
      <div className="compare-heading">
        <strong>Запуски с evaluation</strong>
        <button className="button" type="button" onClick={() => {
          onMapRunChange(null)
          setRefreshKey((value) => value + 1)
        }} disabled={!projectId || listLoading || comparing}>Обновить</button>
      </div>
      {listLoading && <p role="status" className="helper-text">Загружаю запуски…</p>}
      {truncated && <p className="warning-text">
        Показаны до 100 последних запусков с evaluation — список ограничен API.
      </p>}
      {runs.length === 0 && !listLoading && <p className="helper-text">
        Для проекта пока нет доступных успешных запусков с evaluation.
      </p>}
      <div className="compare-options" role="group" aria-label="Выбор запусков">
        {runs.map((run) => {
          const checked = selectedIds.includes(run.id)
          return (
            <label className="compare-option" key={run.id}>
              <input type="checkbox" checked={checked}
                disabled={comparing || (!checked && selectedIds.length >= MAX_COMPARE_RUNS)}
                onChange={(event) => toggleRun(run.id, event.target.checked)} />
              <span><strong>{runLabel(run.id)} · seed {run.seed}</strong>
                <small>{run.mode} · score {numberLabel(run.composite_score)}</small></span>
              {selectedIds[0] === run.id && <span className="compare-baseline-mark">база</span>}
            </label>
          )
        })}
      </div>
      {selectedIds.length > 0 && <div className="compare-selected">
        <strong>Порядок сравнения</strong>
        {selectedIds.map((runId, index) => (
          <div className="compare-selected-row" key={runId}>
            <span>{index + 1}. {runLabel(runId)} {index === 0 ? '· база' : ''}</span>
            {index > 0 && <button type="button" className="button"
              disabled={comparing} onClick={() => changeBaseline(runId)}>Сделать базой</button>}
          </div>
        ))}
      </div>}
      <button className="button button-primary" type="button"
        disabled={!projectId || comparing || selectedIds.length < MIN_COMPARE_RUNS}
        onClick={() => { void compare() }}>
        {comparing ? 'Сравниваю…' : `Сравнить ${selectedIds.length} запуска(ов)`}
      </button>
      {error && <p role="alert" className="form-error">{error}</p>}

      {comparison && (
        <div className="compare-result">
          <p className="helper-text">
            Baseline {runLabel(comparison.baseline_run_id)} ·
            общий рабочий SRID {comparison.runs[0]?.working_srid} ·
            {comparison.scores_comparable
              ? ' политики score совпадают'
              : ' политики score отличаются: ранги и Δ score недоступны'}
          </p>
          <h3>Запуск на карте</h3>
          <div className="compare-map-switch" role="group" aria-label="Отображаемый запуск">
            {comparison.runs.map((run) => (
              <button type="button" key={run.run_id} className="button"
                aria-pressed={mapRunId === run.run_id}
                onClick={() => onMapRunChange(run.run_id)}>
                {runLabel(run.run_id)}{run.run_id === comparison.baseline_run_id ? ' · база' : ''}
              </button>
            ))}
          </div>
          <p className="helper-text">
            Выбранный run применяется ко всем generated/validation слоям карты.
            Если слой не опубликован для run, он остаётся пустым — данные соседнего run не подставляются.
            Исходные слои берутся из выбранной DatasetVersion: для разных входных версий сверяйте provenance.
          </p>
          <div className="compare-table-wrap">
            <table className="compare-table">
              <caption>Persisted score и нарушения ограничений</caption>
              <thead><tr><th scope="col">Показатель</th>
                {comparison.runs.map((run) => <th scope="col" key={run.run_id}>
                  {runLabel(run.run_id)}{run.run_id === comparison.baseline_run_id ? ' (база)' : ''}
                </th>)}
              </tr></thead>
              <tbody>
                <tr><th scope="row">Composite score</th>
                  {comparison.runs.map((run) => <td key={run.run_id}>
                    <strong>{numberLabel(run.composite_score)}</strong>
                    {comparison.scores_comparable &&
                      <small>Δ {deltaLabel(run.score_delta_from_baseline)} · rank {run.score_rank ?? '—'}</small>}
                  </td>)}</tr>
                {([
                  ['Нарушения · всего', 'violation_count'],
                  ['Жёсткие', 'hard_violation_count'],
                  ['Мягкие', 'soft_violation_count'],
                  ['Пространственные', 'spatial_violation_count'],
                ] as const).map(([label, key]) => (
                  <tr key={key}><th scope="row">{label}</th>
                    {comparison.runs.map((run) => <td key={run.run_id}>
                      {run.validation[key]}
                    </td>)}</tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="compare-table-wrap">
            <table className="compare-table">
              <caption>Канонические сохранённые raw metrics</caption>
              <thead><tr><th scope="col">Метрика · единица / направление</th>
                {comparison.runs.map((run) => <th scope="col" key={run.run_id}>
                  {runLabel(run.run_id)}
                </th>)}
              </tr></thead>
              <tbody>
                {comparison.metrics.map((metric) => (
                  <tr key={metric.metric_id}>
                    <th scope="row">
                      <strong>{metric.metric_id}</strong>
                      <small>{metric.unit} · {metric.direction} · v{metric.definition_version}</small>
                    </th>
                    {metric.values.map((value) => (
                      <td key={value.run_id}>
                        <strong>{numberLabel(value.raw_value)}</strong>
                        <small>Δ {deltaLabel(value.delta_from_baseline)} · rank {value.rank ?? '—'}</small>
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </section>
  )
}
