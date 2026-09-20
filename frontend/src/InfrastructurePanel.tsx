import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
} from 'react'
import type {
  ExpressionSpecification,
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
const SOURCE_ID = 'infrastructure-ui-source'

const EXISTING_POINT_ID = 'infrastructure-existing-point'
const EXISTING_FILL_ID = 'infrastructure-existing-fill'
const EXISTING_LINE_ID = 'infrastructure-existing-line'
const GENERATED_POINT_ID = 'infrastructure-generated-point'
const GENERATED_FILL_ID = 'infrastructure-generated-fill'
const GENERATED_LINE_ID = 'infrastructure-generated-line'

const INFRASTRUCTURE_LAYER_IDS = [
  EXISTING_POINT_ID,
  EXISTING_FILL_ID,
  EXISTING_LINE_ID,
  GENERATED_POINT_ID,
  GENERATED_FILL_ID,
  GENERATED_LINE_ID,
] as const

type LoadStatus = 'idle' | 'loading' | 'ready' | 'error'
type Origin = 'existing' | 'generated'

type InfrastructureRunSummary = {
  id: string
  project_id: string
  status: string
  mode: string
  seed: number
  working_srid: number
  existing_facility_count: number
  generated_facility_count: number
  created_at: string
  finished_at: string | null
}

type InfrastructureResponse = GeoJsonFeatureCollection & {
  project_id: string
  run_id: string
  query_bbox: [number, number, number, number]
  geojson_crs: 'EPSG:4326'
  working_srid: number
  limit: number
  truncated: boolean
}

type SelectedInfrastructure = {
  feature: GeoJsonFeature
  origin: Origin
}

type InfrastructurePanelProps = {
  apiBase: string
  map: MapLibreMap | null
  projectId: string | null
}

const GENERATED_CATEGORY_COLOR: ExpressionSpecification = [
  'match',
  ['get', 'category'],
  'education',
  '#2563eb',
  'healthcare',
  '#dc2626',
  'retail',
  '#ca8a04',
  'recreation',
  '#16a34a',
  '#7c3aed',
]

function originFilter(origin: Origin): ExpressionSpecification {
  return ['==', ['get', 'origin'], origin] as ExpressionSpecification
}

function geometryFilter(
  origin: Origin,
  geometryType: 'Point' | 'LineString' | 'Polygon',
): ExpressionSpecification {
  return [
    'all',
    originFilter(origin),
    ['==', ['geometry-type'], geometryType],
  ] as ExpressionSpecification
}

function ensureLayers(map: MapLibreMap): void {
  if (!map.getSource(SOURCE_ID)) {
    map.addSource(SOURCE_ID, {
      type: 'geojson',
      data: EMPTY_FEATURE_COLLECTION,
    })
  }

  if (!map.getLayer(EXISTING_FILL_ID)) {
    map.addLayer({
      id: EXISTING_FILL_ID,
      type: 'fill',
      source: SOURCE_ID,
      filter: geometryFilter('existing', 'Polygon'),
      paint: {
        'fill-color': '#64748b',
        'fill-opacity': 0.28,
      },
    })
  }
  if (!map.getLayer(EXISTING_LINE_ID)) {
    map.addLayer({
      id: EXISTING_LINE_ID,
      type: 'line',
      source: SOURCE_ID,
      filter: originFilter('existing'),
      paint: {
        'line-color': '#475569',
        'line-width': 2,
        'line-opacity': 0.9,
      },
    })
  }
  if (!map.getLayer(EXISTING_POINT_ID)) {
    map.addLayer({
      id: EXISTING_POINT_ID,
      type: 'circle',
      source: SOURCE_ID,
      filter: geometryFilter('existing', 'Point'),
      paint: {
        'circle-color': '#475569',
        'circle-radius': ['interpolate', ['linear'], ['zoom'], 8, 4, 15, 7],
        'circle-stroke-color': '#f8fafc',
        'circle-stroke-width': 1.5,
      },
    })
  }

  if (!map.getLayer(GENERATED_FILL_ID)) {
    map.addLayer({
      id: GENERATED_FILL_ID,
      type: 'fill',
      source: SOURCE_ID,
      filter: geometryFilter('generated', 'Polygon'),
      paint: {
        'fill-color': GENERATED_CATEGORY_COLOR,
        'fill-opacity': 0.48,
      },
    })
  }
  if (!map.getLayer(GENERATED_LINE_ID)) {
    map.addLayer({
      id: GENERATED_LINE_ID,
      type: 'line',
      source: SOURCE_ID,
      filter: originFilter('generated'),
      paint: {
        'line-color': GENERATED_CATEGORY_COLOR,
        'line-width': 2.2,
        'line-opacity': 0.95,
      },
    })
  }
  if (!map.getLayer(GENERATED_POINT_ID)) {
    map.addLayer({
      id: GENERATED_POINT_ID,
      type: 'circle',
      source: SOURCE_ID,
      filter: geometryFilter('generated', 'Point'),
      paint: {
        'circle-color': GENERATED_CATEGORY_COLOR,
        'circle-radius': ['interpolate', ['linear'], ['zoom'], 8, 5, 15, 8],
        'circle-stroke-color': '#ffffff',
        'circle-stroke-width': 2,
      },
    })
  }
}

function setData(map: MapLibreMap, data: GeoJsonFeatureCollection): void {
  const source = map.getSource(SOURCE_ID) as GeoJSONSource | undefined
  source?.setData(data)
}

function mapFeature(feature: MapGeoJSONFeature): SelectedInfrastructure {
  const properties = feature.properties ?? {}
  const origin: Origin =
    properties.origin === 'existing' ? 'existing' : 'generated'
  const fallback =
    properties.candidate_id ??
    properties.source_feature_id ??
    properties.facility_class ??
    feature.layer.id
  return {
    origin,
    feature: {
      type: 'Feature',
      id: String(feature.id ?? fallback),
      geometry: feature.geometry as GeoJsonGeometry,
      properties: { ...properties },
    },
  }
}

function textProperty(
  properties: Record<string, unknown>,
  key: string,
): string | null {
  const value = properties[key]
  return typeof value === 'string' && value.length > 0 ? value : null
}

function numberProperty(
  properties: Record<string, unknown>,
  key: string,
): number | null {
  const value = properties[key]
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function formatRun(run: InfrastructureRunSummary): string {
  return (
    run.id.slice(0, 8) +
    ' · ' +
    run.status +
    ' · fixed ' +
    String(run.existing_facility_count) +
    ' / generated ' +
    String(run.generated_facility_count)
  )
}

function formatCapacity(value: number | null): string {
  return value === null ? '—' : new Intl.NumberFormat().format(value)
}

function formatDistance(value: number | null): string {
  if (value === null) return '—'
  return value >= 1000 ? (value / 1000).toFixed(2) + ' km' : value.toFixed(1) + ' m'
}

function titleFor(selected: SelectedInfrastructure): string {
  const properties = selected.feature.properties
  if (selected.origin === 'generated') {
    return (
      textProperty(properties, 'infrastructure_type_code') ??
      textProperty(properties, 'candidate_id') ??
      'Generated facility'
    )
  }
  return (
    textProperty(properties, 'name') ??
    textProperty(properties, 'facility_class') ??
    'Existing facility'
  )
}

export function InfrastructurePanel({
  apiBase,
  map,
  projectId,
}: InfrastructurePanelProps) {
  const abortRef = useRef<AbortController | null>(null)
  const [runs, setRuns] = useState<InfrastructureRunSummary[]>([])
  const [runId, setRunId] = useState('')
  const [existingVisible, setExistingVisible] = useState(true)
  const [generatedVisible, setGeneratedVisible] = useState(true)
  const [status, setStatus] = useState<LoadStatus>('idle')
  const [message, setMessage] = useState('Выберите проект с infrastructure run.')
  const [existingCount, setExistingCount] = useState(0)
  const [generatedCount, setGeneratedCount] = useState(0)
  const [truncated, setTruncated] = useState(false)
  const [selected, setSelected] = useState<SelectedInfrastructure | null>(null)

  const activeRun = useMemo(
    () => runs.find((run) => run.id === runId) ?? null,
    [runId, runs],
  )

  useEffect(() => {
    if (!map) return
    ensureLayers(map)
    return () => setData(map, EMPTY_FEATURE_COLLECTION)
  }, [map])

  useEffect(() => {
    if (!map) return
    ensureLayers(map)
    for (const layerId of [
      EXISTING_POINT_ID,
      EXISTING_FILL_ID,
      EXISTING_LINE_ID,
    ]) {
      if (map.getLayer(layerId)) {
        map.setLayoutProperty(
          layerId,
          'visibility',
          existingVisible ? 'visible' : 'none',
        )
      }
    }
    for (const layerId of [
      GENERATED_POINT_ID,
      GENERATED_FILL_ID,
      GENERATED_LINE_ID,
    ]) {
      if (map.getLayer(layerId)) {
        map.setLayoutProperty(
          layerId,
          'visibility',
          generatedVisible ? 'visible' : 'none',
        )
      }
    }
  }, [existingVisible, generatedVisible, map])

  useEffect(() => {
    if (!map) return
    const handleClick = (event: MapMouseEvent): void => {
      const layers = INFRASTRUCTURE_LAYER_IDS.filter((id) => map.getLayer(id))
      if (layers.length === 0) return
      const hit = map.queryRenderedFeatures(event.point, { layers })[0]
      if (hit) setSelected(mapFeature(hit))
    }
    map.on('click', handleClick)
    return () => {
      map.off('click', handleClick)
    }
  }, [map])

  useEffect(() => {
    abortRef.current?.abort()
    setRuns([])
    setRunId('')
    setSelected(null)
    setExistingCount(0)
    setGeneratedCount(0)
    setTruncated(false)
    if (map) setData(map, EMPTY_FEATURE_COLLECTION)

    if (!projectId) {
      setStatus('idle')
      setMessage('Выберите проект с infrastructure run.')
      return
    }

    const controller = new AbortController()
    abortRef.current = controller
    setStatus('loading')
    setMessage('Загружаю infrastructure runs…')

    const url =
      apiBase +
      '/projects/' +
      encodeURIComponent(projectId) +
      '/infrastructure-runs'
    void fetch(url, { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) {
          throw new Error(
            'Infrastructure runs: HTTP ' +
              response.status +
              ' ' +
              (await response.text()),
          )
        }
        return (await response.json()) as InfrastructureRunSummary[]
      })
      .then((items) => {
        if (controller.signal.aborted) return
        setRuns(items)
        const preferred =
          items.find(
            (run) =>
              run.existing_facility_count > 0 ||
              run.generated_facility_count > 0,
          ) ?? items[0]
        setRunId(preferred?.id ?? '')
        setStatus('ready')
        setMessage(
          preferred
            ? 'Infrastructure run выбран; карта обновляется по viewport.'
            : 'Для проекта ещё нет generation runs.',
        )
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return
        setStatus('error')
        setMessage(error instanceof Error ? error.message : String(error))
      })

    return () => controller.abort()
  }, [apiBase, map, projectId])

  const loadViewport = useCallback(async () => {
    if (!map || !projectId || !runId) return
    ensureLayers(map)

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
    setStatus('loading')
    setMessage('Загружаю infrastructure facilities для viewport…')

    const url =
      apiBase +
      '/projects/' +
      encodeURIComponent(projectId) +
      '/infrastructure-runs/' +
      encodeURIComponent(runId) +
      '/facilities/geojson?bbox=' +
      encodeURIComponent(bbox) +
      '&limit=' +
      String(VIEWPORT_LIMIT)

    try {
      const response = await fetch(url, { signal: controller.signal })
      if (!response.ok) {
        throw new Error(
          'Infrastructure viewport: HTTP ' +
            response.status +
            ' ' +
            (await response.text()),
        )
      }
      const result = (await response.json()) as InfrastructureResponse
      if (controller.signal.aborted) return

      const visibleFeatures = result.features.filter((feature) => {
        const origin = feature.properties.origin
        if (origin === 'existing') return existingVisible
        if (origin === 'generated') return generatedVisible
        return false
      })
      setData(map, {
        type: 'FeatureCollection',
        features: visibleFeatures,
      })
      setExistingCount(
        result.features.filter(
          (feature) => feature.properties.origin === 'existing',
        ).length,
      )
      setGeneratedCount(
        result.features.filter(
          (feature) => feature.properties.origin === 'generated',
        ).length,
      )
      setTruncated(result.truncated)
      setStatus('ready')
      setMessage(
        'Viewport: fixed ' +
          String(
            result.features.filter(
              (feature) => feature.properties.origin === 'existing',
            ).length,
          ) +
          ', generated ' +
          String(
            result.features.filter(
              (feature) => feature.properties.origin === 'generated',
            ).length,
          ) +
          '.',
      )
    } catch (error: unknown) {
      if (controller.signal.aborted) return
      setStatus('error')
      setMessage(error instanceof Error ? error.message : String(error))
    }
  }, [
    apiBase,
    existingVisible,
    generatedVisible,
    map,
    projectId,
    runId,
  ])

  useEffect(() => {
    if (!map || !runId) return
    const refresh = () => void loadViewport()
    map.on('moveend', refresh)
    void loadViewport()
    return () => {
      map.off('moveend', refresh)
      abortRef.current?.abort()
    }
  }, [loadViewport, map, runId])

  function changeRun(event: ChangeEvent<HTMLSelectElement>): void {
    setRunId(event.target.value)
    setSelected(null)
  }

  const properties = selected?.feature.properties ?? {}

  return (
    <section className="panel infrastructure-panel">
      <div className="section-heading section-heading-row">
        <div>
          <p className="section-kicker">S10 Infrastructure</p>
          <h2>Facilities & accessibility</h2>
        </div>
        <span className="badge">
          {existingCount + generatedCount}
          {truncated ? '+' : ''}
        </span>
      </div>

      <label className="infrastructure-select">
        <span>Generation run</span>
        <select value={runId} onChange={changeRun} disabled={runs.length === 0}>
          {runs.length === 0 && <option value="">Нет infrastructure runs</option>}
          {runs.map((run) => (
            <option value={run.id} key={run.id}>
              {formatRun(run)}
            </option>
          ))}
        </select>
      </label>

      <div className="infrastructure-layer-list">
        <label className="infrastructure-layer-row">
          <input
            type="checkbox"
            checked={existingVisible}
            onChange={(event) => setExistingVisible(event.target.checked)}
          />
          <span className="swatch infrastructure-existing-swatch" />
          <span>Existing / fixed facilities</span>
          <span className="layer-count">
            {activeRun?.existing_facility_count ?? 0}
          </span>
        </label>
        <label className="infrastructure-layer-row">
          <input
            type="checkbox"
            checked={generatedVisible}
            onChange={(event) => setGeneratedVisible(event.target.checked)}
          />
          <span className="swatch infrastructure-generated-swatch" />
          <span>Generated sites / hosts</span>
          <span className="layer-count">
            {activeRun?.generated_facility_count ?? 0}
          </span>
        </label>
      </div>

      <div className="infrastructure-legend">
        <span><i className="infra-education" /> education</span>
        <span><i className="infra-healthcare" /> healthcare</span>
        <span><i className="infra-retail" /> retail</span>
        <span><i className="infra-recreation" /> recreation</span>
      </div>

      <button
        className="button"
        type="button"
        onClick={() => void loadViewport()}
        disabled={!runId}
      >
        Обновить viewport
      </button>

      <div className={'load-state load-state-' + status}>{message}</div>

      <div className="infrastructure-authority-note">
        <strong>Authoritative read boundary</strong>
        <span>
          Карта показывает только persisted facilities выбранного run.
          Candidate alternatives, spatial unmet demand и service-radius matrix
          backend пока не публикует, поэтому браузер их не реконструирует.
        </span>
      </div>

      <div className="infrastructure-inspector">
        <h3>{selected ? titleFor(selected) : 'Facility inspector'}</h3>
        {selected ? (
          <>
            <dl className="infrastructure-metrics">
              <div>
                <dt>Origin</dt>
                <dd>{selected.origin}</dd>
              </div>
              <div>
                <dt>Category / class</dt>
                <dd>
                  {textProperty(properties, 'category') ??
                    textProperty(properties, 'facility_class') ??
                    '—'}
                </dd>
              </div>
              <div>
                <dt>Capacity</dt>
                <dd>{formatCapacity(numberProperty(properties, 'capacity'))}</dd>
              </div>
              <div>
                <dt>Geometry</dt>
                <dd>
                  {textProperty(properties, 'geometry_kind') ??
                    selected.feature.geometry.type}
                </dd>
              </div>
              <div>
                <dt>Network node</dt>
                <dd>{textProperty(properties, 'network_node_id') ?? '—'}</dd>
              </div>
              <div>
                <dt>Snap distance</dt>
                <dd>
                  {formatDistance(
                    numberProperty(properties, 'network_snap_distance_m'),
                  )}
                </dd>
              </div>
            </dl>

            <details className="infrastructure-details">
              <summary>Все persisted атрибуты</summary>
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
          </>
        ) : (
          <p className="helper-text">
            Кликните по existing facility или generated site/host anchor.
            Для generated объекта inspector показывает persisted network snap
            provenance, а не заново рассчитанную accessibility.
          </p>
        )}
      </div>
    </section>
  )
}
