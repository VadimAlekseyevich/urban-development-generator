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
const FACILITY_SOURCE_ID = 'infrastructure-ui-source'
const DEMAND_SOURCE_ID = 'infrastructure-demand-ui-source'

const DEMAND_FILL_ID = 'infrastructure-demand-fill'
const DEMAND_LINE_ID = 'infrastructure-demand-line'
const EXISTING_POINT_ID = 'infrastructure-existing-point'
const EXISTING_FILL_ID = 'infrastructure-existing-fill'
const EXISTING_LINE_ID = 'infrastructure-existing-line'
const GENERATED_POINT_ID = 'infrastructure-generated-point'
const GENERATED_FILL_ID = 'infrastructure-generated-fill'
const GENERATED_LINE_ID = 'infrastructure-generated-line'

const FACILITY_LAYER_IDS = [
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

type InfrastructureDemandResponse = GeoJsonFeatureCollection & {
  project_id: string
  run_id: string
  query_bbox: [number, number, number, number]
  geojson_crs: 'EPSG:4326'
  working_srid: number
  limit: number
  truncated: boolean
}

type InfrastructureAgeCoverage = {
  demographic_group: string
  population: number
  covered_population: number
  coverage_ratio: number
}

type InfrastructureRawMetric = {
  metric_id: string
  scalar_value: number | null
  age_coverage: InfrastructureAgeCoverage[]
}

type InfrastructureAccessibilitySummary = {
  origin: Origin
  infrastructure_type_code: string
  max_network_distance_m: number
  reachable_demand_count: number
  nearest_distance_m: number | null
  farthest_distance_m: number | null
  facility_id: string | null
  source_ref: string | null
  source_feature_id: string | null
  candidate_id: string | null
  acceptance_index: number | null
  capacity: number | null
  network_snapshot_id: string | null
}

type InfrastructureMetricsResponse = {
  project_id: string
  run_id: string
  read_model_version: string
  scenario_version: string
  scenario_fingerprint: string
  raw_metrics: InfrastructureRawMetric[]
  diagnostics: Record<string, number>
  facility_accessibility: InfrastructureAccessibilitySummary[]
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

const DEMAND_FILL_COLOR: ExpressionSpecification = [
  'interpolate',
  ['linear'],
  ['coalesce', ['to-number', ['get', 'final_unmet_demand']], 0],
  0,
  '#f8fafc',
  1,
  '#fde68a',
  10,
  '#fb923c',
  50,
  '#dc2626',
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
  if (!map.getSource(DEMAND_SOURCE_ID)) {
    map.addSource(DEMAND_SOURCE_ID, {
      type: 'geojson',
      data: EMPTY_FEATURE_COLLECTION,
    })
  }
  if (!map.getSource(FACILITY_SOURCE_ID)) {
    map.addSource(FACILITY_SOURCE_ID, {
      type: 'geojson',
      data: EMPTY_FEATURE_COLLECTION,
    })
  }

  if (!map.getLayer(DEMAND_FILL_ID)) {
    map.addLayer({
      id: DEMAND_FILL_ID,
      type: 'fill',
      source: DEMAND_SOURCE_ID,
      paint: {
        'fill-color': DEMAND_FILL_COLOR,
        'fill-opacity': 0.42,
      },
    })
  }
  if (!map.getLayer(DEMAND_LINE_ID)) {
    map.addLayer({
      id: DEMAND_LINE_ID,
      type: 'line',
      source: DEMAND_SOURCE_ID,
      paint: {
        'line-color': '#9a3412',
        'line-width': 1.1,
        'line-opacity': 0.72,
      },
    })
  }

  if (!map.getLayer(EXISTING_FILL_ID)) {
    map.addLayer({
      id: EXISTING_FILL_ID,
      type: 'fill',
      source: FACILITY_SOURCE_ID,
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
      source: FACILITY_SOURCE_ID,
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
      source: FACILITY_SOURCE_ID,
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
      source: FACILITY_SOURCE_ID,
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
      source: FACILITY_SOURCE_ID,
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
      source: FACILITY_SOURCE_ID,
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

function setSourceData(
  map: MapLibreMap,
  sourceId: string,
  data: GeoJsonFeatureCollection,
): void {
  const source = map.getSource(sourceId) as GeoJSONSource | undefined
  source?.setData(data)
}

function mapGeoJsonFeature(feature: MapGeoJSONFeature): GeoJsonFeature {
  const properties = feature.properties ?? {}
  const fallback =
    properties.block_key ??
    properties.candidate_id ??
    properties.source_feature_id ??
    properties.facility_class ??
    feature.layer.id
  return {
    type: 'Feature',
    id: String(feature.id ?? fallback),
    geometry: feature.geometry as GeoJsonGeometry,
    properties: { ...properties },
  }
}

function mapFacilityFeature(feature: MapGeoJSONFeature): SelectedInfrastructure {
  const mapped = mapGeoJsonFeature(feature)
  const origin: Origin =
    mapped.properties.origin === 'existing' ? 'existing' : 'generated'
  return { origin, feature: mapped }
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

function formatNumber(value: number | null, digits = 1): string {
  if (value === null) return '—'
  return new Intl.NumberFormat(undefined, {
    maximumFractionDigits: digits,
  }).format(value)
}

function formatCapacity(value: number | null): string {
  return value === null ? '—' : new Intl.NumberFormat().format(value)
}

function formatDistance(value: number | null): string {
  if (value === null) return '—'
  return value >= 1000
    ? (value / 1000).toFixed(2) + ' km'
    : value.toFixed(1) + ' m'
}

function formatPercent(value: number | null): string {
  return value === null ? '—' : (value * 100).toFixed(1) + '%'
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

function scalarMetric(
  metrics: InfrastructureMetricsResponse | null,
  metricId: string,
): number | null {
  const value = metrics?.raw_metrics.find(
    (metric) => metric.metric_id === metricId,
  )?.scalar_value
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

export function InfrastructurePanel({
  apiBase,
  map,
  projectId,
}: InfrastructurePanelProps) {
  const runsAbortRef = useRef<AbortController | null>(null)
  const viewportAbortRef = useRef<AbortController | null>(null)
  const metricsAbortRef = useRef<AbortController | null>(null)

  const [runs, setRuns] = useState<InfrastructureRunSummary[]>([])
  const [runId, setRunId] = useState('')
  const [existingVisible, setExistingVisible] = useState(true)
  const [generatedVisible, setGeneratedVisible] = useState(true)
  const [demandVisible, setDemandVisible] = useState(true)
  const [status, setStatus] = useState<LoadStatus>('idle')
  const [message, setMessage] = useState('Выберите проект с infrastructure run.')
  const [existingCount, setExistingCount] = useState(0)
  const [generatedCount, setGeneratedCount] = useState(0)
  const [demandCount, setDemandCount] = useState(0)
  const [existingTruncated, setExistingTruncated] = useState(false)
  const [generatedTruncated, setGeneratedTruncated] = useState(false)
  const [demandTruncated, setDemandTruncated] = useState(false)
  const [metrics, setMetrics] = useState<InfrastructureMetricsResponse | null>(null)
  const [readModelReady, setReadModelReady] = useState(false)
  const [selected, setSelected] = useState<SelectedInfrastructure | null>(null)
  const [selectedDemand, setSelectedDemand] = useState<GeoJsonFeature | null>(null)

  const activeRun = useMemo(
    () => runs.find((run) => run.id === runId) ?? null,
    [runId, runs],
  )

  const selectedAccessibility = useMemo(() => {
    if (!selected || !metrics) return null
    const properties = selected.feature.properties
    if (selected.origin === 'generated') {
      const candidateId = textProperty(properties, 'candidate_id')
      const typeCode = textProperty(properties, 'infrastructure_type_code')
      if (!candidateId || !typeCode) return null
      return (
        metrics.facility_accessibility.find(
          (item) =>
            item.origin === 'generated' &&
            item.candidate_id === candidateId &&
            item.infrastructure_type_code === typeCode,
        ) ?? null
      )
    }

    const sourceFeatureId = textProperty(properties, 'source_feature_id')
    if (!sourceFeatureId) return null
    const matches = metrics.facility_accessibility.filter(
      (item) =>
        item.origin === 'existing' &&
        item.source_feature_id === sourceFeatureId,
    )
    return matches.length === 1 ? matches[0] : null
  }, [metrics, selected])

  useEffect(() => {
    if (!map) return
    ensureLayers(map)
    return () => {
      setSourceData(map, FACILITY_SOURCE_ID, EMPTY_FEATURE_COLLECTION)
      setSourceData(map, DEMAND_SOURCE_ID, EMPTY_FEATURE_COLLECTION)
    }
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
    for (const layerId of [DEMAND_FILL_ID, DEMAND_LINE_ID]) {
      if (map.getLayer(layerId)) {
        map.setLayoutProperty(
          layerId,
          'visibility',
          demandVisible ? 'visible' : 'none',
        )
      }
    }
  }, [demandVisible, existingVisible, generatedVisible, map])

  useEffect(() => {
    if (!map) return
    const handleClick = (event: MapMouseEvent): void => {
      const facilityLayers = FACILITY_LAYER_IDS.filter((id) => map.getLayer(id))
      const facilityHit =
        facilityLayers.length > 0
          ? map.queryRenderedFeatures(event.point, {
              layers: facilityLayers,
            })[0]
          : undefined
      if (facilityHit) {
        setSelected(mapFacilityFeature(facilityHit))
        setSelectedDemand(null)
        return
      }

      if (demandVisible && map.getLayer(DEMAND_FILL_ID)) {
        const demandHit = map.queryRenderedFeatures(event.point, {
          layers: [DEMAND_FILL_ID],
        })[0]
        if (demandHit) {
          setSelectedDemand(mapGeoJsonFeature(demandHit))
          setSelected(null)
          return
        }
      }

      setSelected(null)
      setSelectedDemand(null)
    }
    map.on('click', handleClick)
    return () => {
      map.off('click', handleClick)
    }
  }, [demandVisible, map])

  useEffect(() => {
    runsAbortRef.current?.abort()
    viewportAbortRef.current?.abort()
    metricsAbortRef.current?.abort()
    setRuns([])
    setRunId('')
    setMetrics(null)
    setReadModelReady(false)
    setSelected(null)
    setSelectedDemand(null)
    setExistingCount(0)
    setGeneratedCount(0)
    setDemandCount(0)
    setExistingTruncated(false)
    setGeneratedTruncated(false)
    setDemandTruncated(false)
    if (map) {
      setSourceData(map, FACILITY_SOURCE_ID, EMPTY_FEATURE_COLLECTION)
      setSourceData(map, DEMAND_SOURCE_ID, EMPTY_FEATURE_COLLECTION)
    }

    if (!projectId) {
      setStatus('idle')
      setMessage('Выберите проект с infrastructure run.')
      return
    }

    const controller = new AbortController()
    runsAbortRef.current = controller
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
        const requested =
          new URLSearchParams(window.location.search).get(
            'infrastructure_run_id',
          ) ?? ''
        const preferred =
          items.find((run) => run.id === requested) ??
          items.find(
            (run) =>
              run.existing_facility_count > 0 ||
              run.generated_facility_count > 0,
          ) ??
          items[0]
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

  useEffect(() => {
    metricsAbortRef.current?.abort()
    setMetrics(null)
    setReadModelReady(false)
    setSelected(null)
    setSelectedDemand(null)
    if (!projectId || !runId) return

    const controller = new AbortController()
    metricsAbortRef.current = controller
    const url =
      apiBase +
      '/projects/' +
      encodeURIComponent(projectId) +
      '/infrastructure-runs/' +
      encodeURIComponent(runId) +
      '/metrics'

    void fetch(url, { signal: controller.signal })
      .then(async (response) => {
        if (response.status === 409) return null
        if (!response.ok) {
          throw new Error(
            'Infrastructure metrics: HTTP ' +
              response.status +
              ' ' +
              (await response.text()),
          )
        }
        return (await response.json()) as InfrastructureMetricsResponse
      })
      .then((value) => {
        if (controller.signal.aborted) return
        setMetrics(value)
        setReadModelReady(value !== null)
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return
        setMetrics(null)
        setReadModelReady(false)
        setStatus('error')
        setMessage(error instanceof Error ? error.message : String(error))
      })

    return () => controller.abort()
  }, [apiBase, projectId, runId])

  const loadViewport = useCallback(async () => {
    if (!map || !projectId || !runId) return
    ensureLayers(map)

    viewportAbortRef.current?.abort()
    const controller = new AbortController()
    viewportAbortRef.current = controller
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
    setMessage('Загружаю infrastructure viewport…')

    const runBase =
      apiBase +
      '/projects/' +
      encodeURIComponent(projectId) +
      '/infrastructure-runs/' +
      encodeURIComponent(runId)
    const facilitiesBase =
      runBase +
      '/facilities/geojson?bbox=' +
      encodeURIComponent(bbox) +
      '&limit=' +
      String(VIEWPORT_LIMIT)

    const fetchOrigin = async (
      origin: Origin,
    ): Promise<InfrastructureResponse> => {
      const response = await fetch(
        facilitiesBase + '&origin=' + encodeURIComponent(origin),
        { signal: controller.signal },
      )
      if (!response.ok) {
        throw new Error(
          'Infrastructure ' +
            origin +
            ' viewport: HTTP ' +
            response.status +
            ' ' +
            (await response.text()),
        )
      }
      return (await response.json()) as InfrastructureResponse
    }

    const fetchDemand = async (): Promise<InfrastructureDemandResponse | null> => {
      const response = await fetch(
        runBase +
          '/demand/geojson?bbox=' +
          encodeURIComponent(bbox) +
          '&limit=' +
          String(VIEWPORT_LIMIT),
        { signal: controller.signal },
      )
      if (response.status === 409) return null
      if (!response.ok) {
        throw new Error(
          'Infrastructure demand viewport: HTTP ' +
            response.status +
            ' ' +
            (await response.text()),
        )
      }
      return (await response.json()) as InfrastructureDemandResponse
    }

    try {
      const [existingResult, generatedResult, demandResult] = await Promise.all([
        existingVisible
          ? fetchOrigin('existing')
          : Promise.resolve<InfrastructureResponse | null>(null),
        generatedVisible
          ? fetchOrigin('generated')
          : Promise.resolve<InfrastructureResponse | null>(null),
        demandVisible && readModelReady
          ? fetchDemand()
          : Promise.resolve<InfrastructureDemandResponse | null>(null),
      ])
      if (controller.signal.aborted) return

      const existingFeatures = existingResult?.features ?? []
      const generatedFeatures = generatedResult?.features ?? []
      setSourceData(map, FACILITY_SOURCE_ID, {
        type: 'FeatureCollection',
        features: [...existingFeatures, ...generatedFeatures],
      })
      setSourceData(
        map,
        DEMAND_SOURCE_ID,
        demandResult ?? EMPTY_FEATURE_COLLECTION,
      )

      setExistingCount(existingFeatures.length)
      setGeneratedCount(generatedFeatures.length)
      setDemandCount(demandResult?.features.length ?? 0)
      setExistingTruncated(existingResult?.truncated ?? false)
      setGeneratedTruncated(generatedResult?.truncated ?? false)
      setDemandTruncated(demandResult?.truncated ?? false)
      setStatus('ready')
      setMessage(
        'Viewport: fixed ' +
          String(existingFeatures.length) +
          (existingResult?.truncated ? '+' : '') +
          ', generated ' +
          String(generatedFeatures.length) +
          (generatedResult?.truncated ? '+' : '') +
          (readModelReady
            ? ', demand blocks ' +
              String(demandResult?.features.length ?? 0) +
              (demandResult?.truncated ? '+' : '')
            : ', read model not materialized') +
          '.',
      )
    } catch (error: unknown) {
      if (controller.signal.aborted) return
      setStatus('error')
      setMessage(error instanceof Error ? error.message : String(error))
    }
  }, [
    apiBase,
    demandVisible,
    existingVisible,
    generatedVisible,
    map,
    projectId,
    readModelReady,
    runId,
  ])

  useEffect(() => {
    if (!map || !runId) return
    const refresh = () => void loadViewport()
    map.on('moveend', refresh)
    void loadViewport()
    return () => {
      map.off('moveend', refresh)
      viewportAbortRef.current?.abort()
    }
  }, [loadViewport, map, runId])

  function changeRun(event: ChangeEvent<HTMLSelectElement>): void {
    const nextRunId = event.target.value
    setRunId(nextRunId)
    setSelected(null)
    setSelectedDemand(null)
    const url = new URL(window.location.href)
    if (nextRunId) {
      url.searchParams.set('infrastructure_run_id', nextRunId)
    } else {
      url.searchParams.delete('infrastructure_run_id')
    }
    window.history.replaceState({}, '', url)
  }

  const properties = selected?.feature.properties ?? {}
  const demandProperties = selectedDemand?.properties ?? {}
  const populationCoverage = scalarMetric(
    metrics,
    'infrastructure.population_coverage_ratio',
  )
  const unmetDemand = scalarMetric(metrics, 'infrastructure.unmet_demand')
  const p50Distance = scalarMetric(
    metrics,
    'infrastructure.network_distance_p50_m',
  )
  const p90Distance = scalarMetric(
    metrics,
    'infrastructure.network_distance_p90_m',
  )
  const utilization = scalarMetric(
    metrics,
    'infrastructure.capacity_utilization',
  )

  return (
    <section className="panel infrastructure-panel">
      <div className="section-heading section-heading-row">
        <div>
          <p className="section-kicker">S10 Infrastructure</p>
          <h2>Facilities & accessibility</h2>
        </div>
        <span className="badge">
          {existingCount + generatedCount}
          {existingTruncated || generatedTruncated ? '+' : ''}
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
        <label className="infrastructure-layer-row">
          <input
            type="checkbox"
            checked={demandVisible}
            onChange={(event) => setDemandVisible(event.target.checked)}
            disabled={!readModelReady}
          />
          <span className="swatch infrastructure-demand-swatch" />
          <span>Final unmet demand</span>
          <span className="layer-count">
            {demandTruncated ? String(demandCount) + '+' : demandCount}
          </span>
        </label>
      </div>

      <div className="infrastructure-legend">
        <span><i className="infra-education" /> education</span>
        <span><i className="infra-healthcare" /> healthcare</span>
        <span><i className="infra-retail" /> retail</span>
        <span><i className="infra-recreation" /> recreation</span>
      </div>

      {metrics && (
        <dl className="infrastructure-summary-metrics">
          <div>
            <dt>Population coverage</dt>
            <dd>{formatPercent(populationCoverage)}</dd>
          </div>
          <div>
            <dt>Unmet demand</dt>
            <dd>{formatNumber(unmetDemand)}</dd>
          </div>
          <div>
            <dt>P50 distance</dt>
            <dd>{formatDistance(p50Distance)}</dd>
          </div>
          <div>
            <dt>P90 distance</dt>
            <dd>{formatDistance(p90Distance)}</dd>
          </div>
          <div>
            <dt>Capacity utilization</dt>
            <dd>{formatPercent(utilization)}</dd>
          </div>
          <div>
            <dt>Demand rows</dt>
            <dd>{metrics.diagnostics.demand_item_count ?? '—'}</dd>
          </div>
        </dl>
      )}

      <button
        className="button"
        type="button"
        onClick={() => void loadViewport()}
        disabled={!runId}
      >
        Обновить viewport
      </button>

      <div className={'load-state load-state-' + status}>{message}</div>
      {(existingTruncated || generatedTruncated || demandTruncated) && (
        <p className="warning-text">
          Infrastructure viewport достиг limit ({VIEWPORT_LIMIT}) для одного
          из слоёв; приблизьте карту.
        </p>
      )}

      <div className="infrastructure-authority-note">
        <strong>Authoritative read boundary</strong>
        <span>
          Facilities, final unmet demand, T07 reachability summaries и T11 raw
          metrics читаются из persisted run read-model. Frontend не запускает
          snapping, routing, placement или metric computation. Unaccepted
          candidate alternatives остаются вне этого read contract.
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
              <div>
                <dt>Reachable demand rows</dt>
                <dd>
                  {selectedAccessibility?.reachable_demand_count ?? '—'}
                </dd>
              </div>
              <div>
                <dt>Service max</dt>
                <dd>
                  {formatDistance(
                    selectedAccessibility?.max_network_distance_m ?? null,
                  )}
                </dd>
              </div>
              <div>
                <dt>Nearest reachable</dt>
                <dd>
                  {formatDistance(
                    selectedAccessibility?.nearest_distance_m ?? null,
                  )}
                </dd>
              </div>
              <div>
                <dt>Farthest reachable</dt>
                <dd>
                  {formatDistance(
                    selectedAccessibility?.farthest_distance_m ?? null,
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
            Кликните по existing facility или generated site/host. Inspector
            показывает persisted snap provenance и T07 reachability summary.
          </p>
        )}
      </div>

      <div className="infrastructure-inspector">
        <h3>
          {selectedDemand
            ? textProperty(demandProperties, 'block_key') ?? 'Demand block'
            : 'Unmet demand inspector'}
        </h3>
        {selectedDemand ? (
          <>
            <dl className="infrastructure-metrics">
              <div>
                <dt>Gross demand</dt>
                <dd>{formatNumber(numberProperty(demandProperties, 'gross_demand'))}</dd>
              </div>
              <div>
                <dt>Served demand</dt>
                <dd>{formatNumber(numberProperty(demandProperties, 'served_demand'))}</dd>
              </div>
              <div>
                <dt>Final unmet</dt>
                <dd>
                  {formatNumber(
                    numberProperty(demandProperties, 'final_unmet_demand'),
                  )}
                </dd>
              </div>
              <div>
                <dt>Coverage</dt>
                <dd>
                  {formatPercent(
                    numberProperty(demandProperties, 'coverage_ratio'),
                  )}
                </dd>
              </div>
            </dl>
            <details className="infrastructure-details">
              <summary>Demand by infrastructure type</summary>
              <pre className="infrastructure-json">
                {printableProperty(demandProperties.demands)}
              </pre>
            </details>
          </>
        ) : (
          <p className="helper-text">
            Кликните по unmet-demand block, чтобы увидеть итоговый остаточный
            спрос после existing service и generated placement.
          </p>
        )}
      </div>
    </section>
  )
}
