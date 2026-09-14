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
const FIXED_SOURCE_ID = 'zoning-fixed-source'
const GENERATED_SOURCE_ID = 'zoning-generated-source'
const FIXED_FILL_ID = 'zoning-fixed-fill'
const FIXED_LINE_ID = 'zoning-fixed-line'
const GENERATED_FILL_ID = 'zoning-generated-fill'
const GENERATED_LINE_ID = 'zoning-generated-line'

const ZONE_LEGEND = [
  { value: 'residential', label: 'Residential', color: '#ea580c' },
  { value: 'mixed', label: 'Mixed', color: '#7c3aed' },
  { value: 'public', label: 'Public', color: '#2563eb' },
  { value: 'recreation', label: 'Recreation', color: '#16a34a' },
] as const

type LoadStatus = 'idle' | 'loading' | 'ready' | 'error'

type ZoningRunSummary = {
  id: string
  project_id: string
  status: string
  mode: string
  seed: number
  working_srid: number
  zone_count: number
  created_at: string
  finished_at: string | null
}

type GeneratedZoneResponse = GeoJsonFeatureCollection & {
  project_id: string
  run_id: string
  query_bbox: [number, number, number, number]
  geojson_crs: 'EPSG:4326'
  working_srid: number
  limit: number
  truncated: boolean
}

type ZoningPanelProps = {
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

function ensureZoningLayers(map: MapLibreMap): void {
  if (!map.getSource(FIXED_SOURCE_ID)) {
    map.addSource(FIXED_SOURCE_ID, { type: 'geojson', data: EMPTY_FEATURE_COLLECTION })
  }
  if (!map.getSource(GENERATED_SOURCE_ID)) {
    map.addSource(GENERATED_SOURCE_ID, { type: 'geojson', data: EMPTY_FEATURE_COLLECTION })
  }

  if (!map.getLayer(FIXED_FILL_ID)) {
    map.addLayer({
      id: FIXED_FILL_ID,
      type: 'fill',
      source: FIXED_SOURCE_ID,
      paint: { 'fill-color': '#64748b', 'fill-opacity': 0.24 },
    })
  }
  if (!map.getLayer(FIXED_LINE_ID)) {
    map.addLayer({
      id: FIXED_LINE_ID,
      type: 'line',
      source: FIXED_SOURCE_ID,
      paint: { 'line-color': '#334155', 'line-width': 1.2 },
    })
  }
  if (!map.getLayer(GENERATED_FILL_ID)) {
    map.addLayer({
      id: GENERATED_FILL_ID,
      type: 'fill',
      source: GENERATED_SOURCE_ID,
      paint: {
        'fill-color': [
          'match',
          ['get', 'zone_class'],
          'residential',
          '#ea580c',
          'mixed',
          '#7c3aed',
          'public',
          '#2563eb',
          'recreation',
          '#16a34a',
          '#64748b',
        ],
        'fill-opacity': 0.48,
      },
    })
  }
  if (!map.getLayer(GENERATED_LINE_ID)) {
    map.addLayer({
      id: GENERATED_LINE_ID,
      type: 'line',
      source: GENERATED_SOURCE_ID,
      paint: { 'line-color': '#0f172a', 'line-width': 1.4 },
    })
  }
}

function formatRun(run: ZoningRunSummary): string {
  return `${run.id.slice(0, 8)} · ${run.status} · ${run.zone_count} zones`
}

export function ZoningPanel({
  apiBase,
  map,
  projectId,
  datasetVersionId,
}: ZoningPanelProps) {
  const abortRef = useRef<AbortController | null>(null)
  const [runs, setRuns] = useState<ZoningRunSummary[]>([])
  const [selectedRunId, setSelectedRunId] = useState('')
  const [fixedVisible, setFixedVisible] = useState(false)
  const [generatedVisible, setGeneratedVisible] = useState(true)
  const [fixedOpacity, setFixedOpacity] = useState(24)
  const [generatedOpacity, setGeneratedOpacity] = useState(48)
  const [fixedCount, setFixedCount] = useState(0)
  const [generatedCount, setGeneratedCount] = useState(0)
  const [fixedTruncated, setFixedTruncated] = useState(false)
  const [generatedTruncated, setGeneratedTruncated] = useState(false)
  const [loadStatus, setLoadStatus] = useState<LoadStatus>('idle')
  const [loadMessage, setLoadMessage] = useState('Выберите контекст проекта.')

  const selectedRun = useMemo(
    () => runs.find((run) => run.id === selectedRunId) ?? null,
    [runs, selectedRunId],
  )

  useEffect(() => {
    if (!map) return
    ensureZoningLayers(map)
  }, [map])

  useEffect(() => {
    if (!map) return
    ensureZoningLayers(map)
    const visibility = fixedVisible ? 'visible' : 'none'
    map.setLayoutProperty(FIXED_FILL_ID, 'visibility', visibility)
    map.setLayoutProperty(FIXED_LINE_ID, 'visibility', visibility)
    map.setPaintProperty(FIXED_FILL_ID, 'fill-opacity', fixedOpacity / 100)
  }, [fixedOpacity, fixedVisible, map])

  useEffect(() => {
    if (!map) return
    ensureZoningLayers(map)
    const visibility = generatedVisible ? 'visible' : 'none'
    map.setLayoutProperty(GENERATED_FILL_ID, 'visibility', visibility)
    map.setLayoutProperty(GENERATED_LINE_ID, 'visibility', visibility)
    map.setPaintProperty(GENERATED_FILL_ID, 'fill-opacity', generatedOpacity / 100)
  }, [generatedOpacity, generatedVisible, map])

  useEffect(() => {
    if (!projectId) {
      setRuns([])
      setSelectedRunId('')
      return
    }

    const controller = new AbortController()
    void fetch(`${apiBase}/projects/${encodeURIComponent(projectId)}/zoning-runs`, {
      signal: controller.signal,
    })
      .then(async (response) => {
        if (!response.ok) {
          throw new Error(`zoning runs: HTTP ${response.status} ${await response.text()}`)
        }
        return (await response.json()) as ZoningRunSummary[]
      })
      .then((nextRuns) => {
        if (controller.signal.aborted) return
        setRuns(nextRuns)
        const requested = new URLSearchParams(window.location.search).get('run_id') ?? ''
        setSelectedRunId((current) => {
          if (nextRuns.some((run) => run.id === requested)) return requested
          if (nextRuns.some((run) => run.id === current)) return current
          return nextRuns.find((run) => run.zone_count > 0)?.id ?? nextRuns[0]?.id ?? ''
        })
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return
        setRuns([])
        setSelectedRunId('')
        setLoadStatus('error')
        setLoadMessage(error instanceof Error ? error.message : String(error))
      })

    return () => controller.abort()
  }, [apiBase, projectId])

  const loadViewport = useCallback(async () => {
    if (!map || !projectId || !datasetVersionId) {
      setLoadStatus('idle')
      setLoadMessage('Выберите контекст проекта.')
      return
    }
    ensureZoningLayers(map)
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
    setLoadMessage('Загружаю zoning для текущего viewport…')

    try {
      let nextFixedCount = 0
      let nextGeneratedCount = 0
      let nextFixedTruncated = false
      let nextGeneratedTruncated = false

      if (fixedVisible) {
        const fixedUrl =
          `${apiBase}/projects/${encodeURIComponent(projectId)}` +
          `/dataset-versions/${encodeURIComponent(datasetVersionId)}` +
          `/source-layers/landuse/geojson` +
          `?bbox=${encodeURIComponent(bbox)}&limit=${VIEWPORT_LIMIT}`
        const response = await fetch(fixedUrl, { signal: controller.signal })
        if (!response.ok) {
          throw new Error(`fixed zones: HTTP ${response.status} ${await response.text()}`)
        }
        const fixed = (await response.json()) as SourceLayerResponse
        const collection: GeoJsonFeatureCollection = {
          type: 'FeatureCollection',
          features: fixed.features,
        }
        setSourceData(map, FIXED_SOURCE_ID, collection)
        nextFixedCount = fixed.features.length
        nextFixedTruncated = fixed.truncated
      } else {
        setSourceData(map, FIXED_SOURCE_ID, EMPTY_FEATURE_COLLECTION)
      }

      if (generatedVisible && selectedRunId) {
        const generatedUrl =
          `${apiBase}/projects/${encodeURIComponent(projectId)}` +
          `/zoning-runs/${encodeURIComponent(selectedRunId)}/zones/geojson` +
          `?bbox=${encodeURIComponent(bbox)}&limit=${VIEWPORT_LIMIT}`
        const response = await fetch(generatedUrl, { signal: controller.signal })
        if (!response.ok) {
          throw new Error(
            `generated zones: HTTP ${response.status} ${await response.text()}`,
          )
        }
        const generated = (await response.json()) as GeneratedZoneResponse
        setSourceData(map, GENERATED_SOURCE_ID, generated)
        nextGeneratedCount = generated.features.length
        nextGeneratedTruncated = generated.truncated
      } else {
        setSourceData(map, GENERATED_SOURCE_ID, EMPTY_FEATURE_COLLECTION)
      }

      if (controller.signal.aborted) return
      setFixedCount(nextFixedCount)
      setGeneratedCount(nextGeneratedCount)
      setFixedTruncated(nextFixedTruncated)
      setGeneratedTruncated(nextGeneratedTruncated)
      setLoadStatus('ready')
      setLoadMessage(
        selectedRunId
          ? `Viewport: fixed ${nextFixedCount}, generated ${nextGeneratedCount}.`
          : `Viewport: fixed ${nextFixedCount}; generated run отсутствует.`,
      )
    } catch (error: unknown) {
      if (controller.signal.aborted) return
      setLoadStatus('error')
      setLoadMessage(error instanceof Error ? error.message : String(error))
    }
  }, [
    apiBase,
    datasetVersionId,
    fixedVisible,
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
    if (runId) url.searchParams.set('run_id', runId)
    else url.searchParams.delete('run_id')
    window.history.replaceState({}, '', url)
  }

  return (
    <section className="panel">
      <div className="section-heading section-heading-row">
        <div>
          <p className="section-kicker">S05 · Zoning</p>
          <h2>Functional zoning</h2>
        </div>
        <span className="badge">{runs.length} runs</span>
      </div>

      <label className="zoning-select">
        <span>Generated run</span>
        <select value={selectedRunId} onChange={changeRun} disabled={!projectId || runs.length === 0}>
          {runs.length === 0 && <option value="">Нет persisted zoning runs</option>}
          {runs.map((run) => (
            <option value={run.id} key={run.id}>
              {formatRun(run)}
            </option>
          ))}
        </select>
      </label>

      <div className="zoning-layer-list">
        <div className="zoning-layer-card">
          <label className="zoning-toggle">
            <input
              type="checkbox"
              checked={fixedVisible}
              onChange={(event) => setFixedVisible(event.target.checked)}
            />
            <span className="swatch zoning-fixed-swatch" />
            <span>Fixed zones</span>
            <span className="layer-count">{fixedTruncated ? `${fixedCount}+` : fixedCount}</span>
          </label>
          <label className="opacity-control">
            Fixed opacity: {fixedOpacity}%
            <input
              type="range"
              min="0"
              max="100"
              value={fixedOpacity}
              onChange={(event) => setFixedOpacity(Number(event.target.value))}
            />
          </label>
        </div>

        <div className="zoning-layer-card">
          <label className="zoning-toggle">
            <input
              type="checkbox"
              checked={generatedVisible}
              onChange={(event) => setGeneratedVisible(event.target.checked)}
            />
            <span className="swatch zoning-generated-swatch" />
            <span>Generated zones</span>
            <span className="layer-count">
              {generatedTruncated ? `${generatedCount}+` : generatedCount}
            </span>
          </label>
          <label className="opacity-control">
            Generated opacity: {generatedOpacity}%
            <input
              type="range"
              min="0"
              max="100"
              value={generatedOpacity}
              onChange={(event) => setGeneratedOpacity(Number(event.target.value))}
            />
          </label>
        </div>
      </div>

      <div className="zoning-legend">
        {ZONE_LEGEND.map((zone) => (
          <span key={zone.value}>
            <i style={{ backgroundColor: zone.color }} />
            {zone.label}
          </span>
        ))}
      </div>

      <div className={`load-state load-state-${loadStatus}`}>{loadMessage}</div>
      {(fixedTruncated || generatedTruncated) && (
        <p className="warning-text">
          Zoning viewport достиг limit ({VIEWPORT_LIMIT}); приблизьте карту.
        </p>
      )}
      <p className="helper-text">
        Fixed zones — неизменяемый source-landuse view текущей DatasetVersion; generated zones
        читаются по выбранному run и не меняют source data.
      </p>
      {selectedRun && (
        <p className="helper-text">
          Run {selectedRun.id.slice(0, 8)} · {selectedRun.mode} · {selectedRun.zone_count} zones
          total.
        </p>
      )}
    </section>
  )
}
