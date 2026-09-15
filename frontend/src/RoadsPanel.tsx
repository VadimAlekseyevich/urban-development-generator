import { useCallback, useEffect, useMemo, useRef, useState, type ChangeEvent } from 'react'
import type { GeoJSONSource, Map as MapLibreMap } from 'maplibre-gl'

import {
  EMPTY_FEATURE_COLLECTION,
  bboxParam,
  viewportBounds,
  type GeoJsonFeatureCollection,
  type SourceLayerResponse,
} from './sourceLayers'

const VIEWPORT_LIMIT = 1500
const EXISTING_SOURCE_ID = 'roads-ui-existing-source'
const GENERATED_SOURCE_ID = 'roads-ui-generated-source'
const EXISTING_LAYER_ID = 'roads-ui-existing-line'
const GENERATED_LAYER_ID = 'roads-ui-generated-line'

const ROAD_LEGEND = [
  { value: 'arterial', label: 'Arterial', color: '#dc2626' },
  { value: 'collector', label: 'Collector', color: '#f59e0b' },
  { value: 'local', label: 'Local', color: '#0ea5e9' },
] as const

type LoadStatus = 'idle' | 'loading' | 'ready' | 'error'

type RoadRunSummary = {
  id: string
  project_id: string
  status: string
  mode: string
  seed: number
  working_srid: number
  generated_road_count: number
  created_at: string
  finished_at: string | null
}

type RoadGraphDiagnostics = {
  run_id: string
  edge_count: number
  road_count: number
  node_count: number
  component_count: number
  dead_end_node_count: number
  dead_end_ratio: number
  total_length_m: number
  class_counts: Record<string, number>
  origin_counts: Record<string, number>
}

type GeneratedRoadResponse = GeoJsonFeatureCollection & {
  project_id: string
  run_id: string
  query_bbox: [number, number, number, number]
  geojson_crs: 'EPSG:4326'
  working_srid: number
  limit: number
  truncated: boolean
}

type RoadsPanelProps = {
  apiBase: string
  map: MapLibreMap | null
  projectId: string | null
  datasetVersionId: string | null
}

function setSourceData(
  map: MapLibreMap,
  sourceId: string,
  data: GeoJsonFeatureCollection,
): void {
  const source = map.getSource(sourceId) as GeoJSONSource | undefined
  source?.setData(data)
}

function ensureRoadLayers(map: MapLibreMap): void {
  if (!map.getSource(EXISTING_SOURCE_ID)) {
    map.addSource(EXISTING_SOURCE_ID, { type: 'geojson', data: EMPTY_FEATURE_COLLECTION })
  }
  if (!map.getSource(GENERATED_SOURCE_ID)) {
    map.addSource(GENERATED_SOURCE_ID, { type: 'geojson', data: EMPTY_FEATURE_COLLECTION })
  }

  if (!map.getLayer(EXISTING_LAYER_ID)) {
    map.addLayer({
      id: EXISTING_LAYER_ID,
      type: 'line',
      source: EXISTING_SOURCE_ID,
      paint: {
        'line-color': '#475569',
        'line-opacity': 0.72,
        'line-width': ['interpolate', ['linear'], ['zoom'], 7, 1.2, 12, 2.4, 16, 4.2],
      },
    })
  }
  if (!map.getLayer(GENERATED_LAYER_ID)) {
    map.addLayer({
      id: GENERATED_LAYER_ID,
      type: 'line',
      source: GENERATED_SOURCE_ID,
      paint: {
        'line-color': [
          'match',
          ['get', 'road_class'],
          'arterial',
          '#dc2626',
          'collector',
          '#f59e0b',
          'local',
          '#0ea5e9',
          '#7c3aed',
        ],
        'line-width': [
          'match',
          ['get', 'road_class'],
          'arterial',
          4.2,
          'collector',
          3.2,
          'local',
          2.2,
          2.6,
        ],
        'line-opacity': 0.92,
      },
    })
  }
}

function formatRun(run: RoadRunSummary): string {
  return `${run.id.slice(0, 8)} · ${run.status} · ${run.generated_road_count} edges`
}

function formatDistance(lengthM: number): string {
  if (lengthM >= 1000) return `${(lengthM / 1000).toFixed(2)} km`
  return `${lengthM.toFixed(0)} m`
}

export function RoadsPanel({
  apiBase,
  map,
  projectId,
  datasetVersionId,
}: RoadsPanelProps) {
  const abortRef = useRef<AbortController | null>(null)
  const diagnosticsAbortRef = useRef<AbortController | null>(null)
  const [runs, setRuns] = useState<RoadRunSummary[]>([])
  const [selectedRunId, setSelectedRunId] = useState('')
  const [existingVisible, setExistingVisible] = useState(false)
  const [generatedVisible, setGeneratedVisible] = useState(true)
  const [existingCount, setExistingCount] = useState(0)
  const [generatedCount, setGeneratedCount] = useState(0)
  const [existingTruncated, setExistingTruncated] = useState(false)
  const [generatedTruncated, setGeneratedTruncated] = useState(false)
  const [diagnostics, setDiagnostics] = useState<RoadGraphDiagnostics | null>(null)
  const [loadStatus, setLoadStatus] = useState<LoadStatus>('idle')
  const [loadMessage, setLoadMessage] = useState('Выберите контекст проекта.')

  const selectedRun = useMemo(
    () => runs.find((run) => run.id === selectedRunId) ?? null,
    [runs, selectedRunId],
  )

  useEffect(() => {
    if (!map) return
    ensureRoadLayers(map)
  }, [map])

  useEffect(() => {
    if (!map) return
    ensureRoadLayers(map)
    map.setLayoutProperty(EXISTING_LAYER_ID, 'visibility', existingVisible ? 'visible' : 'none')
  }, [existingVisible, map])

  useEffect(() => {
    if (!map) return
    ensureRoadLayers(map)
    map.setLayoutProperty(
      GENERATED_LAYER_ID,
      'visibility',
      generatedVisible ? 'visible' : 'none',
    )
  }, [generatedVisible, map])

  useEffect(() => {
    if (!projectId) {
      setRuns([])
      setSelectedRunId('')
      setDiagnostics(null)
      return
    }

    const controller = new AbortController()
    void fetch(`${apiBase}/projects/${encodeURIComponent(projectId)}/road-runs`, {
      signal: controller.signal,
    })
      .then(async (response) => {
        if (!response.ok) {
          throw new Error(`road runs: HTTP ${response.status} ${await response.text()}`)
        }
        return (await response.json()) as RoadRunSummary[]
      })
      .then((nextRuns) => {
        if (controller.signal.aborted) return
        setRuns(nextRuns)
        const requested = new URLSearchParams(window.location.search).get('road_run_id') ?? ''
        setSelectedRunId((current) => {
          if (nextRuns.some((run) => run.id === requested)) return requested
          if (nextRuns.some((run) => run.id === current)) return current
          return (
            nextRuns.find((run) => run.generated_road_count > 0)?.id ?? nextRuns[0]?.id ?? ''
          )
        })
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return
        setRuns([])
        setSelectedRunId('')
        setDiagnostics(null)
        setLoadStatus('error')
        setLoadMessage(error instanceof Error ? error.message : String(error))
      })

    return () => controller.abort()
  }, [apiBase, projectId])

  useEffect(() => {
    diagnosticsAbortRef.current?.abort()
    setDiagnostics(null)
    if (!projectId || !selectedRunId) return

    const controller = new AbortController()
    diagnosticsAbortRef.current = controller
    const url =
      `${apiBase}/projects/${encodeURIComponent(projectId)}` +
      `/road-runs/${encodeURIComponent(selectedRunId)}/diagnostics`
    void fetch(url, { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) {
          throw new Error(`road diagnostics: HTTP ${response.status} ${await response.text()}`)
        }
        return (await response.json()) as RoadGraphDiagnostics
      })
      .then((nextDiagnostics) => {
        if (!controller.signal.aborted) setDiagnostics(nextDiagnostics)
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return
        setLoadStatus('error')
        setLoadMessage(error instanceof Error ? error.message : String(error))
      })

    return () => controller.abort()
  }, [apiBase, projectId, selectedRunId])

  const loadViewport = useCallback(async () => {
    if (!map || !projectId || !datasetVersionId) {
      setLoadStatus('idle')
      setLoadMessage('Выберите контекст проекта.')
      return
    }
    ensureRoadLayers(map)
    abortRef.current?.abort()
    const controller = new AbortController()
    abortRef.current = controller

    const bounds = map.getBounds()
    const bbox = bboxParam(
      viewportBounds(
        bounds.getWest(),
        bounds.getSouth(),
        bounds.getEast(),
        bounds.getNorth(),
      ),
    )
    setLoadStatus('loading')
    setLoadMessage('Загружаю road network для текущего viewport…')

    try {
      let nextExistingCount = 0
      let nextGeneratedCount = 0
      let nextExistingTruncated = false
      let nextGeneratedTruncated = false

      if (existingVisible) {
        const existingUrl =
          `${apiBase}/projects/${encodeURIComponent(projectId)}` +
          `/dataset-versions/${encodeURIComponent(datasetVersionId)}` +
          `/source-layers/roads/geojson` +
          `?bbox=${encodeURIComponent(bbox)}&limit=${VIEWPORT_LIMIT}`
        const response = await fetch(existingUrl, { signal: controller.signal })
        if (!response.ok) {
          throw new Error(`existing roads: HTTP ${response.status} ${await response.text()}`)
        }
        const existing = (await response.json()) as SourceLayerResponse
        setSourceData(map, EXISTING_SOURCE_ID, {
          type: 'FeatureCollection',
          features: existing.features,
        })
        nextExistingCount = existing.features.length
        nextExistingTruncated = existing.truncated
      } else {
        setSourceData(map, EXISTING_SOURCE_ID, EMPTY_FEATURE_COLLECTION)
      }

      if (generatedVisible && selectedRunId) {
        const generatedUrl =
          `${apiBase}/projects/${encodeURIComponent(projectId)}` +
          `/road-runs/${encodeURIComponent(selectedRunId)}/roads/geojson` +
          `?bbox=${encodeURIComponent(bbox)}&limit=${VIEWPORT_LIMIT}`
        const response = await fetch(generatedUrl, { signal: controller.signal })
        if (!response.ok) {
          throw new Error(
            `generated roads: HTTP ${response.status} ${await response.text()}`,
          )
        }
        const generated = (await response.json()) as GeneratedRoadResponse
        setSourceData(map, GENERATED_SOURCE_ID, generated)
        nextGeneratedCount = generated.features.length
        nextGeneratedTruncated = generated.truncated
      } else {
        setSourceData(map, GENERATED_SOURCE_ID, EMPTY_FEATURE_COLLECTION)
      }

      if (controller.signal.aborted) return
      setExistingCount(nextExistingCount)
      setGeneratedCount(nextGeneratedCount)
      setExistingTruncated(nextExistingTruncated)
      setGeneratedTruncated(nextGeneratedTruncated)
      setLoadStatus('ready')
      setLoadMessage(
        selectedRunId
          ? `Viewport: existing ${nextExistingCount}, generated ${nextGeneratedCount}.`
          : `Viewport: existing ${nextExistingCount}; generated run отсутствует.`,
      )
    } catch (error: unknown) {
      if (controller.signal.aborted) return
      setLoadStatus('error')
      setLoadMessage(error instanceof Error ? error.message : String(error))
    }
  }, [
    apiBase,
    datasetVersionId,
    existingVisible,
    generatedVisible,
    map,
    projectId,
    selectedRunId,
  ])

  useEffect(() => {
    if (!map) return
    const handleMoveEnd = () => void loadViewport()
    map.on('moveend', handleMoveEnd)
    void loadViewport()
    return () => {
      map.off('moveend', handleMoveEnd)
      abortRef.current?.abort()
    }
  }, [loadViewport, map])

  function changeRun(event: ChangeEvent<HTMLSelectElement>): void {
    const runId = event.target.value
    setSelectedRunId(runId)
    const url = new URL(window.location.href)
    if (runId) url.searchParams.set('road_run_id', runId)
    else url.searchParams.delete('road_run_id')
    window.history.replaceState({}, '', url)
  }

  return (
    <section className="panel roads-panel">
      <div className="section-heading section-heading-row">
        <div>
          <p className="section-kicker">S06 · Roads</p>
          <h2>Road network</h2>
        </div>
        <span className="badge">{runs.length} runs</span>
      </div>

      <label className="roads-select">
        <span>Generated run</span>
        <select value={selectedRunId} onChange={changeRun} disabled={!projectId || runs.length === 0}>
          {runs.length === 0 && <option value="">Нет persisted road runs</option>}
          {runs.map((run) => (
            <option value={run.id} key={run.id}>
              {formatRun(run)}
            </option>
          ))}
        </select>
      </label>

      <div className="roads-layer-list">
        <label className="roads-layer-row">
          <input
            type="checkbox"
            checked={existingVisible}
            onChange={(event) => setExistingVisible(event.target.checked)}
          />
          <span className="swatch roads-existing-swatch" />
          <span>Existing / fixed</span>
          <span className="layer-count">
            {existingTruncated ? `${existingCount}+` : existingCount}
          </span>
        </label>
        <label className="roads-layer-row">
          <input
            type="checkbox"
            checked={generatedVisible}
            onChange={(event) => setGeneratedVisible(event.target.checked)}
          />
          <span className="swatch roads-generated-swatch" />
          <span>Generated delta</span>
          <span className="layer-count">
            {generatedTruncated ? `${generatedCount}+` : generatedCount}
          </span>
        </label>
      </div>

      <div className="roads-legend">
        {ROAD_LEGEND.map((roadClass) => (
          <span key={roadClass.value}>
            <i style={{ backgroundColor: roadClass.color }} />
            {roadClass.label}
          </span>
        ))}
      </div>

      {diagnostics && (
        <div className="roads-diagnostics" aria-label="Road graph diagnostics">
          <div>
            <span>Edges</span>
            <strong>{diagnostics.edge_count}</strong>
          </div>
          <div>
            <span>Logical roads</span>
            <strong>{diagnostics.road_count}</strong>
          </div>
          <div>
            <span>Nodes</span>
            <strong>{diagnostics.node_count}</strong>
          </div>
          <div>
            <span>Components</span>
            <strong>{diagnostics.component_count}</strong>
          </div>
          <div>
            <span>Dead ends</span>
            <strong>
              {diagnostics.dead_end_node_count} ({(diagnostics.dead_end_ratio * 100).toFixed(1)}%)
            </strong>
          </div>
          <div>
            <span>Generated length</span>
            <strong>{formatDistance(diagnostics.total_length_m)}</strong>
          </div>
        </div>
      )}

      {diagnostics && Object.keys(diagnostics.class_counts).length > 0 && (
        <p className="helper-text roads-class-summary">
          Classes:{' '}
          {Object.entries(diagnostics.class_counts)
            .map(([roadClass, count]) => `${roadClass} ${count}`)
            .join(' · ')}
        </p>
      )}

      <div className={`load-state load-state-${loadStatus}`}>{loadMessage}</div>
      {(existingTruncated || generatedTruncated) && (
        <p className="warning-text">
          Roads viewport достиг limit ({VIEWPORT_LIMIT}); приблизьте карту.
        </p>
      )}
      <p className="helper-text">
        Existing roads читаются из immutable SourceRoad текущей DatasetVersion; generated delta —
        из выбранного GenerationRun. Цвет generated-линий кодирует road class.
      </p>
      {selectedRun && (
        <p className="helper-text">
          Run {selectedRun.id.slice(0, 8)} · {selectedRun.mode} ·{' '}
          {selectedRun.generated_road_count} persisted edges total.
        </p>
      )}
    </section>
  )
}
