import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
} from 'react'
import type {
  GeoJSONSource,
  Map as MapLibreMap,
  MapGeoJSONFeature,
  MapMouseEvent,
} from 'maplibre-gl'

import {
  EMPTY_FEATURE_COLLECTION,
  bboxParam,
  printableProperty,
  viewportBounds,
  type GeoJsonFeature,
  type GeoJsonFeatureCollection,
  type GeoJsonGeometry,
} from './sourceLayers'

const VIEWPORT_LIMIT = 1500
const BUILDING_SOURCE_ID = 'generated-buildings-ui-source'
const BUILDING_FILL_ID = 'generated-buildings-ui-fill'
const BUILDING_LINE_ID = 'generated-buildings-ui-line'

type LoadStatus = 'idle' | 'loading' | 'ready' | 'error'

type BuildingRunSummary = {
  id: string
  project_id: string
  status: string
  mode: string
  seed: number
  working_srid: number
  generated_building_count: number
  created_at: string
  finished_at: string | null
}

type BuildingResponse = GeoJsonFeatureCollection & {
  project_id: string
  run_id: string
  query_bbox: [number, number, number, number]
  geojson_crs: 'EPSG:4326'
  working_srid: number
  limit: number
  truncated: boolean
}

type BuildingsPanelProps = {
  apiBase: string
  map: MapLibreMap | null
  projectId: string | null
}

function setSourceData(
  map: MapLibreMap,
  data: GeoJsonFeatureCollection,
): void {
  const source = map.getSource(BUILDING_SOURCE_ID) as GeoJSONSource | undefined
  source?.setData(data)
}

function ensureLayers(map: MapLibreMap): void {
  if (!map.getSource(BUILDING_SOURCE_ID)) {
    map.addSource(BUILDING_SOURCE_ID, {
      type: 'geojson',
      data: EMPTY_FEATURE_COLLECTION,
    })
  }

  if (!map.getLayer(BUILDING_FILL_ID)) {
    map.addLayer({
      id: BUILDING_FILL_ID,
      type: 'fill',
      source: BUILDING_SOURCE_ID,
      paint: {
        'fill-color': [
          'match',
          ['get', 'archetype'],
          'detached',
          '#f59e0b',
          'point',
          '#f97316',
          'bar',
          '#2563eb',
          'perimeter',
          '#7c3aed',
          'courtyard',
          '#0d9488',
          'public',
          '#dc2626',
          'commercial',
          '#ca8a04',
          '#64748b',
        ],
        'fill-opacity': 0.48,
      },
    })
  }

  if (!map.getLayer(BUILDING_LINE_ID)) {
    map.addLayer({
      id: BUILDING_LINE_ID,
      type: 'line',
      source: BUILDING_SOURCE_ID,
      paint: {
        'line-color': [
          'match',
          ['get', 'building_use'],
          'residential',
          '#166534',
          'mixed',
          '#1d4ed8',
          'public',
          '#b91c1c',
          'commercial',
          '#a16207',
          '#334155',
        ],
        'line-width': [
          'interpolate',
          ['linear'],
          ['zoom'],
          10,
          0.6,
          15,
          1.2,
          18,
          2,
        ],
      },
    })
  }
}

function formatRun(run: BuildingRunSummary): string {
  return (
    `${run.id.slice(0, 8)} · ${run.status} · ` +
    `${run.generated_building_count} buildings`
  )
}

function mapFeature(feature: MapGeoJSONFeature): GeoJsonFeature {
  const properties = feature.properties ?? {}
  const fallback = properties.building_key ?? feature.layer.id
  return {
    type: 'Feature',
    id: String(feature.id ?? fallback),
    geometry: feature.geometry as GeoJsonGeometry,
    properties: { ...properties },
  }
}

function textProperty(
  properties: Record<string, unknown>,
  key: string,
): string | null {
  const value = properties[key]
  return typeof value === 'string' && value ? value : null
}

function numberProperty(
  properties: Record<string, unknown>,
  key: string,
): number | null {
  const value = properties[key]
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function formatArea(value: number | null): string {
  if (value === null) return '—'
  return `${value.toFixed(value >= 1000 ? 0 : 1)} m²`
}

export function BuildingsPanel({
  apiBase,
  map,
  projectId,
}: BuildingsPanelProps) {
  const abortRef = useRef<AbortController | null>(null)
  const [runs, setRuns] = useState<BuildingRunSummary[]>([])
  const [selectedRunId, setSelectedRunId] = useState('')
  const [visible, setVisible] = useState(true)
  const [count, setCount] = useState(0)
  const [truncated, setTruncated] = useState(false)
  const [selected, setSelected] = useState<GeoJsonFeature | null>(null)
  const [loadStatus, setLoadStatus] = useState<LoadStatus>('idle')
  const [loadMessage, setLoadMessage] = useState('Выберите контекст проекта.')

  const selectedRun = useMemo(
    () => runs.find((run) => run.id === selectedRunId) ?? null,
    [runs, selectedRunId],
  )

  useEffect(() => {
    if (!map) return
    ensureLayers(map)
  }, [map])

  useEffect(() => {
    if (!map) return
    ensureLayers(map)
    for (const id of [BUILDING_FILL_ID, BUILDING_LINE_ID]) {
      map.setLayoutProperty(id, 'visibility', visible ? 'visible' : 'none')
    }
  }, [map, visible])

  useEffect(() => {
    if (!map) return
    const handleClick = (event: MapMouseEvent): void => {
      if (!visible || !map.getLayer(BUILDING_FILL_ID)) return
      const feature = map.queryRenderedFeatures(event.point, {
        layers: [BUILDING_FILL_ID, BUILDING_LINE_ID],
      })[0]
      setSelected(feature ? mapFeature(feature) : null)
    }
    map.on('click', handleClick)
    return () => {
      map.off('click', handleClick)
    }
  }, [map, visible])

  useEffect(() => {
    abortRef.current?.abort()
    setRuns([])
    setSelectedRunId('')
    setSelected(null)
    setCount(0)
    setTruncated(false)
    if (map) setSourceData(map, EMPTY_FEATURE_COLLECTION)

    if (!projectId) {
      setLoadStatus('idle')
      setLoadMessage('Выберите контекст проекта.')
      return
    }

    const controller = new AbortController()
    abortRef.current = controller
    setLoadStatus('loading')
    setLoadMessage('Загружаю building runs…')

    void fetch(
      `${apiBase}/projects/${encodeURIComponent(projectId)}/building-runs`,
      { signal: controller.signal },
    )
      .then(async (response) => {
        if (!response.ok) {
          throw new Error(`building runs: HTTP ${response.status} ${await response.text()}`)
        }
        return (await response.json()) as BuildingRunSummary[]
      })
      .then((nextRuns) => {
        if (controller.signal.aborted) return
        setRuns(nextRuns)
        const preferred =
          nextRuns.find((run) => run.generated_building_count > 0) ?? nextRuns[0]
        setSelectedRunId(preferred?.id ?? '')
        setLoadStatus('ready')
        setLoadMessage(
          preferred
            ? `Run ${preferred.id.slice(0, 8)} выбран.`
            : 'В проекте пока нет generation runs.',
        )
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return
        setLoadStatus('error')
        setLoadMessage(error instanceof Error ? error.message : String(error))
      })

    return () => controller.abort()
  }, [apiBase, map, projectId])

  const loadViewport = useCallback(async () => {
    if (!map || !projectId || !selectedRunId || !visible) {
      if (map && !visible) setSourceData(map, EMPTY_FEATURE_COLLECTION)
      return
    }

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
    setLoadMessage('Загружаю здания для текущего viewport…')

    const url =
      `${apiBase}/projects/${encodeURIComponent(projectId)}` +
      `/building-runs/${encodeURIComponent(selectedRunId)}/buildings/geojson` +
      `?bbox=${encodeURIComponent(bbox)}&limit=${VIEWPORT_LIMIT}`
    try {
      const response = await fetch(url, { signal: controller.signal })
      if (!response.ok) {
        throw new Error(`buildings: HTTP ${response.status} ${await response.text()}`)
      }
      const payload = (await response.json()) as BuildingResponse
      if (controller.signal.aborted) return
      const collection: GeoJsonFeatureCollection = {
        type: 'FeatureCollection',
        features: payload.features,
      }
      setSourceData(map, collection)
      setCount(payload.features.length)
      setTruncated(payload.truncated)
      setLoadStatus('ready')
      setLoadMessage(
        `В viewport: ${payload.features.length}` +
          (payload.truncated ? '+' : '') +
          ' зданий.',
      )
    } catch (error: unknown) {
      if (controller.signal.aborted) return
      setLoadStatus('error')
      setLoadMessage(error instanceof Error ? error.message : String(error))
    }
  }, [apiBase, map, projectId, selectedRunId, visible])

  useEffect(() => {
    if (!map || !selectedRunId) return
    const handleMoveEnd = () => void loadViewport()
    map.on('moveend', handleMoveEnd)
    void loadViewport()
    return () => {
      map.off('moveend', handleMoveEnd)
    }
  }, [loadViewport, map, selectedRunId])

  function changeRun(event: ChangeEvent<HTMLSelectElement>): void {
    setSelectedRunId(event.target.value)
    setSelected(null)
  }

  const properties = selected?.properties ?? {}
  const title =
    textProperty(properties, 'building_key') ??
    (selected ? `building: ${selected.id}` : null)

  return (
    <section className="panel buildings-panel">
      <div className="section-heading section-heading-row">
        <div>
          <p className="section-kicker">Generated buildings</p>
          <h2>Застройка</h2>
        </div>
        <span className="badge">
          {truncated ? `${count}+` : count}
        </span>
      </div>

      <label className="buildings-select">
        <span>Generation run</span>
        <select value={selectedRunId} onChange={changeRun} disabled={!runs.length}>
          {!runs.length && <option value="">Нет runs</option>}
          {runs.map((run) => (
            <option value={run.id} key={run.id}>
              {formatRun(run)}
            </option>
          ))}
        </select>
      </label>

      <label className="buildings-layer-row">
        <input
          type="checkbox"
          checked={visible}
          onChange={() => setVisible((current) => !current)}
        />
        <span className="swatch buildings-swatch" />
        <span>Generated buildings</span>
        <span className="layer-count">
          {selectedRun?.generated_building_count ?? 0}
        </span>
      </label>

      <div className="buildings-legend">
        <span><i className="building-point" /> point/detached</span>
        <span><i className="building-bar" /> bar</span>
        <span><i className="building-perimeter" /> perimeter</span>
        <span><i className="building-courtyard" /> courtyard</span>
        <span><i className="building-public" /> public</span>
        <span><i className="building-commercial" /> commercial</span>
      </div>

      <button
        className="button"
        type="button"
        onClick={() => void loadViewport()}
        disabled={!selectedRunId || !visible}
      >
        Обновить viewport
      </button>

      <div className={`load-state load-state-${loadStatus}`}>{loadMessage}</div>
      {truncated && (
        <p className="warning-text">
          Достигнут viewport limit ({VIEWPORT_LIMIT}); приблизьте карту.
        </p>
      )}

      {selected && title ? (
        <div className="buildings-inspector">
          <h3>{title}</h3>
          <dl className="buildings-metrics">
            <div>
              <dt>Archetype</dt>
              <dd>{textProperty(properties, 'archetype') ?? '—'}</dd>
            </div>
            <div>
              <dt>Use</dt>
              <dd>{textProperty(properties, 'building_use') ?? '—'}</dd>
            </div>
            <div>
              <dt>Floors</dt>
              <dd>{numberProperty(properties, 'floors') ?? '—'}</dd>
            </div>
            <div>
              <dt>Footprint</dt>
              <dd>{formatArea(numberProperty(properties, 'footprint_area_m2'))}</dd>
            </div>
            <div>
              <dt>GFA</dt>
              <dd>{formatArea(numberProperty(properties, 'gfa_m2'))}</dd>
            </div>
            <div>
              <dt>Zone</dt>
              <dd>{textProperty(properties, 'zone_class') ?? '—'}</dd>
            </div>
          </dl>
          <details className="buildings-details">
            <summary>Все атрибуты</summary>
            <dl className="property-grid">
              {Object.entries(properties)
                .filter(([, value]) => value !== null && value !== undefined)
                .map(([key, value]) => (
                  <div key={key}>
                    <dt>{key}</dt>
                    <dd>{printableProperty(value)}</dd>
                  </div>
                ))}
            </dl>
          </details>
        </div>
      ) : (
        <p className="helper-text">
          Кликните по generated building, чтобы увидеть use, archetype, этажность и GFA.
        </p>
      )}
    </section>
  )
}
