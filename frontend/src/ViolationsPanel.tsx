import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
} from 'react'
import {
  type GeoJSONSource,
  type Map as MapLibreMap,
  type MapMouseEvent,
} from 'maplibre-gl'

import {
  fetchValidationRuns,
  fetchViolationGeoJSON,
  fetchViolations,
  type ValidationRunSummary,
  type ViolationDetail,
} from './validationApi'
import {
  EMPTY_FEATURE_COLLECTION,
  viewportBounds,
  type GeoJsonFeatureCollection,
} from './sourceLayers'

const SOURCE_ID = 'validation-violations'
const FILL_LAYER_ID = 'validation-violations-fill'
const OUTLINE_LAYER_ID = 'validation-violations-outline'
const LINE_LAYER_ID = 'validation-violations-line'
const POINT_LAYER_ID = 'validation-violations-point'
const LAYER_IDS = [
  FILL_LAYER_ID,
  OUTLINE_LAYER_ID,
  LINE_LAYER_ID,
  POINT_LAYER_ID,
] as const

type LoadStatus = 'idle' | 'loading' | 'ready' | 'error'

type ViolationSelection = {
  violation_index: number
  code: string
  severity: 'HARD' | 'SOFT'
  scope: string
  message: string
  entity_id: string | null
}

type ViolationsPanelProps = {
  apiBase: string
  map: MapLibreMap | null
  projectId: string | null
}

function ensureViolationLayers(map: MapLibreMap): void {
  if (!map.getSource(SOURCE_ID)) {
    map.addSource(SOURCE_ID, {
      type: 'geojson',
      data: EMPTY_FEATURE_COLLECTION,
    })
  }

  if (!map.getLayer(FILL_LAYER_ID)) {
    map.addLayer({
      id: FILL_LAYER_ID,
      type: 'fill',
      source: SOURCE_ID,
      filter: ['==', ['geometry-type'], 'Polygon'],
      paint: {
        'fill-color': [
          'match',
          ['get', 'severity'],
          'HARD',
          '#dc2626',
          'SOFT',
          '#f59e0b',
          '#64748b',
        ],
        'fill-opacity': 0.24,
      },
    })
  }
  if (!map.getLayer(OUTLINE_LAYER_ID)) {
    map.addLayer({
      id: OUTLINE_LAYER_ID,
      type: 'line',
      source: SOURCE_ID,
      filter: ['==', ['geometry-type'], 'Polygon'],
      paint: {
        'line-color': [
          'match',
          ['get', 'severity'],
          'HARD',
          '#dc2626',
          'SOFT',
          '#f59e0b',
          '#64748b',
        ],
        'line-width': 3,
        'line-opacity': 0.95,
      },
    })
  }
  if (!map.getLayer(LINE_LAYER_ID)) {
    map.addLayer({
      id: LINE_LAYER_ID,
      type: 'line',
      source: SOURCE_ID,
      filter: ['==', ['geometry-type'], 'LineString'],
      paint: {
        'line-color': [
          'match',
          ['get', 'severity'],
          'HARD',
          '#dc2626',
          'SOFT',
          '#f59e0b',
          '#64748b',
        ],
        'line-width': 4,
        'line-opacity': 0.95,
      },
    })
  }
  if (!map.getLayer(POINT_LAYER_ID)) {
    map.addLayer({
      id: POINT_LAYER_ID,
      type: 'circle',
      source: SOURCE_ID,
      filter: ['==', ['geometry-type'], 'Point'],
      paint: {
        'circle-color': [
          'match',
          ['get', 'severity'],
          'HARD',
          '#dc2626',
          'SOFT',
          '#f59e0b',
          '#64748b',
        ],
        'circle-radius': 7,
        'circle-stroke-color': '#ffffff',
        'circle-stroke-width': 1.5,
      },
    })
  }
}

function setViolationData(
  map: MapLibreMap,
  data: GeoJsonFeatureCollection,
): void {
  const source = map.getSource(SOURCE_ID) as GeoJSONSource | undefined
  source?.setData(data)
}

function selectionFromDetail(detail: ViolationDetail): ViolationSelection {
  return {
    violation_index: detail.violation_index,
    code: detail.code,
    severity: detail.severity,
    scope: detail.scope,
    message: detail.message,
    entity_id: detail.entity_id,
  }
}

function selectionFromProperties(
  properties: Record<string, unknown>,
): ViolationSelection | null {
  const rawIndex = properties.violation_index
  const index =
    typeof rawIndex === 'number'
      ? rawIndex
      : typeof rawIndex === 'string'
        ? Number(rawIndex)
        : Number.NaN
  const code = properties.code
  const severity = properties.severity
  const scope = properties.scope
  const message = properties.message
  const entityId = properties.entity_id
  if (
    !Number.isInteger(index) ||
    index < 0 ||
    typeof code !== 'string' ||
    (severity !== 'HARD' && severity !== 'SOFT') ||
    typeof scope !== 'string' ||
    typeof message !== 'string'
  ) {
    return null
  }
  return {
    violation_index: index,
    code,
    severity,
    scope,
    message,
    entity_id: typeof entityId === 'string' ? entityId : null,
  }
}

function formatRun(run: ValidationRunSummary): string {
  return `${run.id.slice(0, 8)} · ${run.status} · ${run.violation_count} violations`
}

export function ViolationsPanel({
  apiBase,
  map,
  projectId,
}: ViolationsPanelProps) {
  const runsAbortRef = useRef<AbortController | null>(null)
  const detailsAbortRef = useRef<AbortController | null>(null)
  const viewportAbortRef = useRef<AbortController | null>(null)
  const [runs, setRuns] = useState<ValidationRunSummary[]>([])
  const [runsTruncated, setRunsTruncated] = useState(false)
  const [selectedRunId, setSelectedRunId] = useState('')
  const [violations, setViolations] = useState<ViolationDetail[]>([])
  const [detailTotal, setDetailTotal] = useState(0)
  const [detailTruncated, setDetailTruncated] = useState(false)
  const [visible, setVisible] = useState(true)
  const [viewportCount, setViewportCount] = useState(0)
  const [viewportTruncated, setViewportTruncated] = useState(false)
  const [selected, setSelected] = useState<ViolationSelection | null>(null)
  const [loadStatus, setLoadStatus] = useState<LoadStatus>('idle')
  const [loadMessage, setLoadMessage] = useState(
    'Выберите проект с persisted validation report.',
  )

  const selectedRun = useMemo(
    () => runs.find((run) => run.id === selectedRunId) ?? null,
    [runs, selectedRunId],
  )

  useEffect(() => {
    if (!map) return
    ensureViolationLayers(map)
  }, [map])

  useEffect(() => {
    if (!map) return
    ensureViolationLayers(map)
    for (const layerId of LAYER_IDS) {
      if (map.getLayer(layerId)) {
        map.setLayoutProperty(
          layerId,
          'visibility',
          visible ? 'visible' : 'none',
        )
      }
    }
    if (!visible) {
      setViolationData(map, EMPTY_FEATURE_COLLECTION)
      setViewportCount(0)
      setViewportTruncated(false)
    }
  }, [map, visible])

  useEffect(() => {
    runsAbortRef.current?.abort()
    setRuns([])
    setRunsTruncated(false)
    setSelectedRunId('')
    setViolations([])
    setSelected(null)
    if (!projectId) {
      setLoadStatus('idle')
      setLoadMessage('Выберите проект с persisted validation report.')
      return
    }

    const controller = new AbortController()
    runsAbortRef.current = controller
    setLoadStatus('loading')
    setLoadMessage('Загружаю validation runs…')
    void fetchValidationRuns(apiBase, projectId, controller.signal)
      .then((response) => {
        if (controller.signal.aborted) return
        setRuns(response.runs)
        setRunsTruncated(response.truncated)
        const requested =
          new URLSearchParams(window.location.search).get(
            'validation_run_id',
          ) ?? ''
        const next =
          response.runs.find((run) => run.id === requested)?.id ??
          response.runs[0]?.id ??
          ''
        setSelectedRunId(next)
        setLoadStatus('ready')
        setLoadMessage(
          response.runs.length
            ? `Validation reports: ${response.runs.length}.`
            : 'У проекта пока нет persisted validation report.',
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
    detailsAbortRef.current?.abort()
    setViolations([])
    setDetailTotal(0)
    setDetailTruncated(false)
    setSelected(null)
    if (!projectId || !selectedRunId) return

    const controller = new AbortController()
    detailsAbortRef.current = controller
    void fetchViolations(
      apiBase,
      projectId,
      selectedRunId,
      controller.signal,
    )
      .then((response) => {
        if (controller.signal.aborted) return
        setViolations(response.violations)
        setDetailTotal(response.total)
        setDetailTruncated(response.truncated)
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return
        setLoadStatus('error')
        setLoadMessage(error instanceof Error ? error.message : String(error))
      })

    return () => controller.abort()
  }, [apiBase, projectId, selectedRunId])

  const loadViewport = useCallback(async () => {
    if (!map || !projectId || !selectedRunId || !visible) {
      if (map) setViolationData(map, EMPTY_FEATURE_COLLECTION)
      setViewportCount(0)
      setViewportTruncated(false)
      return
    }
    ensureViolationLayers(map)
    viewportAbortRef.current?.abort()
    const controller = new AbortController()
    viewportAbortRef.current = controller
    const bounds = map.getBounds()
    const viewport = viewportBounds(
      bounds.getWest(),
      bounds.getSouth(),
      bounds.getEast(),
      bounds.getNorth(),
    )
    try {
      const response = await fetchViolationGeoJSON(
        apiBase,
        projectId,
        selectedRunId,
        viewport,
        controller.signal,
      )
      if (controller.signal.aborted) return
      setViolationData(map, response)
      setViewportCount(response.features.length)
      setViewportTruncated(response.truncated)
      setLoadStatus('ready')
      setLoadMessage(
        `Violations: ${detailTotal} total · ${response.matching_count} spatial in viewport.`,
      )
    } catch (error: unknown) {
      if (controller.signal.aborted) return
      setViolationData(map, EMPTY_FEATURE_COLLECTION)
      setViewportCount(0)
      setViewportTruncated(false)
      setLoadStatus('error')
      setLoadMessage(error instanceof Error ? error.message : String(error))
    }
  }, [
    apiBase,
    detailTotal,
    map,
    projectId,
    selectedRunId,
    visible,
  ])

  useEffect(() => {
    if (!map) return
    const handleMoveEnd = () => void loadViewport()
    map.on('moveend', handleMoveEnd)
    void loadViewport()
    return () => {
      map.off('moveend', handleMoveEnd)
      viewportAbortRef.current?.abort()
    }
  }, [loadViewport, map])

  useEffect(() => {
    if (!map) return

    const handleClick = (event: MapMouseEvent) => {
      const layers = LAYER_IDS.filter((layerId) => map.getLayer(layerId))
      const feature = map.queryRenderedFeatures(event.point, { layers })[0]
      if (!feature) return
      const selection = selectionFromProperties(
        feature.properties as Record<string, unknown>,
      )
      if (selection) setSelected(selection)
    }

    const handleMouseMove = (event: MapMouseEvent) => {
      const layers = LAYER_IDS.filter((layerId) => map.getLayer(layerId))
      if (map.queryRenderedFeatures(event.point, { layers }).length > 0) {
        map.getCanvas().style.cursor = 'pointer'
      }
    }

    map.on('click', handleClick)
    map.on('mousemove', handleMouseMove)
    return () => {
      map.off('click', handleClick)
      map.off('mousemove', handleMouseMove)
    }
  }, [map])

  function changeRun(event: ChangeEvent<HTMLSelectElement>): void {
    const runId = event.target.value
    setSelectedRunId(runId)
    const url = new URL(window.location.href)
    if (runId) url.searchParams.set('validation_run_id', runId)
    else url.searchParams.delete('validation_run_id')
    window.history.replaceState({}, '', url)
  }

  return (
    <section className="panel violations-panel">
      <div className="section-heading section-heading-row">
        <div>
          <p className="section-kicker">S11 · Validation</p>
          <h2>Violations</h2>
        </div>
        <span className="badge">{selectedRun?.violation_count ?? 0}</span>
      </div>

      <label className="violations-select">
        <span>Validation run</span>
        <select
          value={selectedRunId}
          onChange={changeRun}
          disabled={!projectId || runs.length === 0}
        >
          {runs.length === 0 && (
            <option value="">Нет persisted validation runs</option>
          )}
          {runs.map((run) => (
            <option value={run.id} key={run.id}>
              {formatRun(run)}
            </option>
          ))}
        </select>
      </label>

      {selectedRun && (
        <div className="violations-stats">
          <div>
            <span>Hard</span>
            <strong>{selectedRun.hard_violation_count}</strong>
          </div>
          <div>
            <span>Soft</span>
            <strong>{selectedRun.soft_violation_count}</strong>
          </div>
          <div>
            <span>Spatial</span>
            <strong>{selectedRun.spatial_violation_count}</strong>
          </div>
        </div>
      )}

      <label className="violations-layer-row">
        <input
          type="checkbox"
          checked={visible}
          onChange={(event) => setVisible(event.target.checked)}
        />
        <span className="violations-swatch" />
        <span>Problem geometries</span>
        <span className="layer-count">
          {viewportTruncated ? `${viewportCount}+` : viewportCount}
        </span>
      </label>

      <div className="violations-legend">
        <span><i className="violations-hard" />HARD</span>
        <span><i className="violations-soft" />SOFT</span>
      </div>

      <div className={`load-state load-state-${loadStatus}`}>
        {loadMessage}
      </div>
      {(runsTruncated || detailTruncated || viewportTruncated) && (
        <p className="warning-text">
          Достигнут validation limit; используйте viewport/пагинацию API для
          полного набора.
        </p>
      )}

      <div className="violations-list" aria-label="Validation violations">
        {violations.slice(0, 12).map((violation) => (
          <button
            type="button"
            key={violation.violation_index}
            className={
              selected?.violation_index === violation.violation_index
                ? 'violation-row violation-row-selected'
                : 'violation-row'
            }
            onClick={() => setSelected(selectionFromDetail(violation))}
          >
            <span
              className={
                violation.severity === 'HARD'
                  ? 'violation-severity violation-severity-hard'
                  : 'violation-severity violation-severity-soft'
              }
            >
              {violation.severity}
            </span>
            <span className="violation-code">{violation.code}</span>
            <span className="violation-entity">
              {violation.entity_id ?? violation.scope}
            </span>
          </button>
        ))}
      </div>
      {detailTotal > 12 && (
        <p className="helper-text">
          Показаны первые 12 из {detailTotal}; API сохраняет детерминированный
          violation_index для полного списка.
        </p>
      )}

      {selected && (
        <div className="violation-inspector">
          <div className="violation-inspector-heading">
            <span
              className={
                selected.severity === 'HARD'
                  ? 'violation-severity violation-severity-hard'
                  : 'violation-severity violation-severity-soft'
              }
            >
              {selected.severity}
            </span>
            <strong>{selected.code}</strong>
          </div>
          <p>{selected.message}</p>
          <dl>
            <div>
              <dt>Scope</dt>
              <dd>{selected.scope}</dd>
            </div>
            <div>
              <dt>Entity</dt>
              <dd>{selected.entity_id ?? '—'}</dd>
            </div>
            <div>
              <dt>Index</dt>
              <dd>{selected.violation_index}</dd>
            </div>
          </dl>
        </div>
      )}

      <p className="helper-text">
        Карта показывает canonical problem_geometry выбранного run;
        non-spatial violations остаются в списке.
      </p>
    </section>
  )
}
