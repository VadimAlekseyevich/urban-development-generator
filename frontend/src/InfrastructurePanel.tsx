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

type LoadStatus = 'idle' | 'loading' | 'ready' | 'partial' | 'error'
type ReadModelStatus = 'idle' | 'loading' | 'ready' | 'not-ready' | 'error'
type Origin = 'existing' | 'generated'
type ViewportLayerKey = 'existing' | 'generated' | 'demand'

type ViewportLayerState = {
  count: number
  truncated: boolean
  error: string | null
}

type ViewportLayerStates = Record<ViewportLayerKey, ViewportLayerState>

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

type BoundedInfrastructureResponse =
  | InfrastructureResponse
  | InfrastructureDemandResponse

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

const VIEWPORT_LABELS: Record<ViewportLayerKey, string> = {
  existing: 'fixed facilities',
  generated: 'generated facilities',
  demand: 'final unmet demand',
}

function emptyViewportLayerStates(): ViewportLayerStates {
  return {
    existing: { count: 0, truncated: false, error: null },
    generated: { count: 0, truncated: false, error: null },
    demand: { count: 0, truncated: false, error: null },
  }
}

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

function syncRunQuery(runId: string): void {
  const url = new URL(window.location.href)
  if (runId) {
    url.searchParams.set('infrastructure_run_id', runId)
  } else {
    url.searchParams.delete('infrastructure_run_id')
  }
  window.history.replaceState({}, '', url)
}

function chooseRun(
  runs: InfrastructureRunSummary[],
  requestedRunId: string,
): { runId: string; notice: string | null } {
  if (requestedRunId) {
    const requested = runs.find((run) => run.id === requestedRunId)
    if (requested) return { runId: requested.id, notice: null }
  }

  const preferred =
    runs.find(
      (run) =>
        run.existing_facility_count > 0 ||
        run.generated_facility_count > 0,
    ) ??
    runs[0] ??
    null
  if (!preferred) return { runId: '', notice: null }

  return {
    runId: preferred.id,
    notice: requestedRunId
      ? 'Requested run ' +
        requestedRunId.slice(0, 8) +
        ' is unavailable; selected ' +
        preferred.id.slice(0, 8) +
        '.'
      : null,
  }
}

function layerCount(
  state: ViewportLayerState,
  total: number | null = null,
): string {
  if (state.error) return '!'
  const visible = String(state.count) + (state.truncated ? '+' : '')
  return total === null ? visible : visible + '/' + String(total)
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
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
  const [runsStatus, setRunsStatus] = useState<LoadStatus>('idle')
  const [runsMessage, setRunsMessage] = useState(
    'Выберите проект с infrastructure run.',
  )
  const [runSelectionNotice, setRunSelectionNotice] = useState<string | null>(
    null,
  )
  const [runsRefreshNonce, setRunsRefreshNonce] = useState(0)

  const [metrics, setMetrics] = useState<InfrastructureMetricsResponse | null>(
    null,
  )
  const [readModelStatus, setReadModelStatus] =
    useState<ReadModelStatus>('idle')
  const [readModelMessage, setReadModelMessage] = useState(
    'Read model будет проверен после выбора run.',
  )
  const [readModelRefreshNonce, setReadModelRefreshNonce] = useState(0)

  const [existingVisible, setExistingVisible] = useState(true)
  const [generatedVisible, setGeneratedVisible] = useState(true)
  const [demandVisible, setDemandVisible] = useState(true)
  const [viewportStatus, setViewportStatus] = useState<LoadStatus>('idle')
  const [viewportMessage, setViewportMessage] = useState(
    'Viewport будет загружен после выбора run.',
  )
  const [viewportLayers, setViewportLayers] = useState<ViewportLayerStates>(
    emptyViewportLayerStates,
  )

  const [selected, setSelected] = useState<SelectedInfrastructure | null>(null)
  const [selectedDemand, setSelectedDemand] = useState<GeoJsonFeature | null>(
    null,
  )

  const activeRun = useMemo(
    () => runs.find((run) => run.id === runId) ?? null,
    [runId, runs],
  )
  const readModelReady = readModelStatus === 'ready'

  const truncatedLayers = useMemo(
    () =>
      (Object.keys(viewportLayers) as ViewportLayerKey[])
        .filter((key) => viewportLayers[key].truncated)
        .map((key) => VIEWPORT_LABELS[key]),
    [viewportLayers],
  )

  const layerErrors = useMemo(
    () =>
      (Object.keys(viewportLayers) as ViewportLayerKey[])
        .filter((key) => viewportLayers[key].error !== null)
        .map((key) => ({
          key,
          label: VIEWPORT_LABELS[key],
          message: viewportLayers[key].error ?? '',
        })),
    [viewportLayers],
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
    setRunSelectionNotice(null)
    setReadModelStatus('idle')
    setReadModelMessage('Read model будет проверен после выбора run.')
    setViewportStatus('idle')
    setViewportMessage('Viewport будет загружен после выбора run.')
    setViewportLayers(emptyViewportLayerStates())
    setSelected(null)
    setSelectedDemand(null)
    if (map) {
      setSourceData(map, FACILITY_SOURCE_ID, EMPTY_FEATURE_COLLECTION)
      setSourceData(map, DEMAND_SOURCE_ID, EMPTY_FEATURE_COLLECTION)
    }

    if (!projectId) {
      setRunsStatus('idle')
      setRunsMessage('Выберите проект с infrastructure run.')
      return
    }

    const controller = new AbortController()
    runsAbortRef.current = controller
    setRunsStatus('loading')
    setRunsMessage('Загружаю infrastructure runs…')

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
        const selection = chooseRun(items, requested)
        setRunId(selection.runId)
        setRunSelectionNotice(selection.notice)
        syncRunQuery(selection.runId)
        setRunsStatus('ready')
        setRunsMessage(
          items.length > 0
            ? String(items.length) + ' infrastructure runs доступны.'
            : 'Для проекта ещё нет generation runs.',
        )
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return
        setRuns([])
        setRunId('')
        syncRunQuery('')
        setRunsStatus('error')
        setRunsMessage(errorMessage(error))
      })

    return () => controller.abort()
  }, [apiBase, map, projectId, runsRefreshNonce])

  useEffect(() => {
    metricsAbortRef.current?.abort()
    setMetrics(null)
    setSelected(null)
    setSelectedDemand(null)

    if (!projectId || !runId) {
      setReadModelStatus('idle')
      setReadModelMessage('Read model будет проверен после выбора run.')
      return
    }

    const controller = new AbortController()
    metricsAbortRef.current = controller
    setReadModelStatus('loading')
    setReadModelMessage('Проверяю persisted infrastructure read model…')
    const url =
      apiBase +
      '/projects/' +
      encodeURIComponent(projectId) +
      '/infrastructure-runs/' +
      encodeURIComponent(runId) +
      '/metrics'

    void fetch(url, { signal: controller.signal })
      .then(async (response) => {
        if (response.status === 409) {
          return {
            kind: 'not-ready' as const,
            detail: await response.text(),
          }
        }
        if (!response.ok) {
          throw new Error(
            'Infrastructure metrics: HTTP ' +
              response.status +
              ' ' +
              (await response.text()),
          )
        }
        return {
          kind: 'ready' as const,
          value: (await response.json()) as InfrastructureMetricsResponse,
        }
      })
      .then((result) => {
        if (controller.signal.aborted) return
        if (result.kind === 'not-ready') {
          setMetrics(null)
          setReadModelStatus('not-ready')
          setReadModelMessage(
            'Run существует, но S10 presentation read model ещё не materialized. ' +
              'Fixed/generated facilities доступны; demand и metrics пока скрыты.',
          )
          if (map) {
            setSourceData(map, DEMAND_SOURCE_ID, EMPTY_FEATURE_COLLECTION)
          }
          setViewportLayers((current) => ({
            ...current,
            demand: { count: 0, truncated: false, error: null },
          }))
          return
        }
        setMetrics(result.value)
        setReadModelStatus('ready')
        setReadModelMessage(
          'Authoritative S10 metrics и demand read model готовы.',
        )
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return
        setMetrics(null)
        setReadModelStatus('error')
        setReadModelMessage(errorMessage(error))
      })

    return () => controller.abort()
  }, [apiBase, map, projectId, readModelRefreshNonce, runId])

  const loadViewport = useCallback(async () => {
    if (!map || !projectId || !runId) {
      setViewportStatus('idle')
      setViewportMessage('Viewport будет загружен после выбора run.')
      return
    }
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
    setViewportStatus('loading')
    setViewportMessage('Загружаю infrastructure viewport…')

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
          'HTTP ' + response.status + ' ' + (await response.text()),
        )
      }
      return (await response.json()) as InfrastructureResponse
    }

    const fetchDemand = async (): Promise<InfrastructureDemandResponse> => {
      const response = await fetch(
        runBase +
          '/demand/geojson?bbox=' +
          encodeURIComponent(bbox) +
          '&limit=' +
          String(VIEWPORT_LIMIT),
        { signal: controller.signal },
      )
      if (!response.ok) {
        throw new Error(
          'HTTP ' + response.status + ' ' + (await response.text()),
        )
      }
      return (await response.json()) as InfrastructureDemandResponse
    }

    const requests: Array<{
      key: ViewportLayerKey
      promise: Promise<BoundedInfrastructureResponse>
    }> = []

    if (existingVisible) {
      requests.push({ key: 'existing', promise: fetchOrigin('existing') })
    }
    if (generatedVisible) {
      requests.push({ key: 'generated', promise: fetchOrigin('generated') })
    }
    if (demandVisible && readModelReady) {
      requests.push({ key: 'demand', promise: fetchDemand() })
    }

    if (requests.length === 0) {
      setSourceData(map, FACILITY_SOURCE_ID, EMPTY_FEATURE_COLLECTION)
      setSourceData(map, DEMAND_SOURCE_ID, EMPTY_FEATURE_COLLECTION)
      setViewportLayers(emptyViewportLayerStates())
      setViewportStatus('ready')
      setViewportMessage(
        readModelStatus === 'not-ready'
          ? 'Facility layers скрыты; demand read model ещё не готов.'
          : 'Все infrastructure layers скрыты.',
      )
      return
    }

    const results = await Promise.allSettled(
      requests.map((request) => request.promise),
    )
    if (controller.signal.aborted) return

    const nextStates = emptyViewportLayerStates()
    let existingFeatures: GeoJsonFeature[] = []
    let generatedFeatures: GeoJsonFeature[] = []
    let demandFeatures: GeoJsonFeature[] = []
    let failureCount = 0

    results.forEach((result, index) => {
      const key = requests[index].key
      if (result.status === 'rejected') {
        failureCount += 1
        nextStates[key] = {
          count: 0,
          truncated: false,
          error: errorMessage(result.reason),
        }
        return
      }

      nextStates[key] = {
        count: result.value.features.length,
        truncated: result.value.truncated,
        error: null,
      }
      if (key === 'existing') {
        existingFeatures = result.value.features
      } else if (key === 'generated') {
        generatedFeatures = result.value.features
      } else {
        demandFeatures = result.value.features
      }
    })

    setSourceData(map, FACILITY_SOURCE_ID, {
      type: 'FeatureCollection',
      features: [...existingFeatures, ...generatedFeatures],
    })
    setSourceData(map, DEMAND_SOURCE_ID, {
      type: 'FeatureCollection',
      features: demandFeatures,
    })
    setViewportLayers(nextStates)

    const successCount = requests.length - failureCount
    if (failureCount === requests.length) {
      setViewportStatus('error')
      setViewportMessage('Не удалось загрузить ни один выбранный слой.')
    } else if (failureCount > 0) {
      setViewportStatus('partial')
      setViewportMessage(
        'Viewport загружен частично: ' +
          String(successCount) +
          '/' +
          String(requests.length) +
          ' слоёв.',
      )
    } else {
      setViewportStatus('ready')
      setViewportMessage(
        'Viewport загружен: ' +
          String(requests.length) +
          '/' +
          String(requests.length) +
          ' слоёв.',
      )
    }
  }, [
    apiBase,
    demandVisible,
    existingVisible,
    generatedVisible,
    map,
    projectId,
    readModelReady,
    readModelStatus,
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
    setRunSelectionNotice(null)
    setMetrics(null)
    setReadModelStatus(nextRunId ? 'loading' : 'idle')
    setViewportStatus(nextRunId ? 'loading' : 'idle')
    setViewportLayers(emptyViewportLayerStates())
    setSelected(null)
    setSelectedDemand(null)
    syncRunQuery(nextRunId)
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
        <span className="badge">{runs.length} runs</span>
      </div>

      <div className="infrastructure-run-controls">
        <label className="infrastructure-select">
          <span>Generation run</span>
          <select
            value={runId}
            onChange={changeRun}
            disabled={runsStatus === 'loading' || runs.length === 0}
          >
            {runs.length === 0 && <option value="">Нет infrastructure runs</option>}
            {runs.map((run) => (
              <option value={run.id} key={run.id}>
                {formatRun(run)}
              </option>
            ))}
          </select>
        </label>
        <button
          className="button"
          type="button"
          onClick={() => setRunsRefreshNonce((value) => value + 1)}
          disabled={!projectId || runsStatus === 'loading'}
        >
          Обновить runs
        </button>
      </div>

      <div
        className={
          'infrastructure-state infrastructure-state-' + runsStatus
        }
      >
        <strong>Run list</strong>
        <span>{runsMessage}</span>
      </div>

      {runSelectionNotice && (
        <div className="infrastructure-selection-notice">
          {runSelectionNotice}
        </div>
      )}

      {activeRun && (
        <dl className="infrastructure-run-meta">
          <div>
            <dt>Status</dt>
            <dd>{activeRun.status}</dd>
          </div>
          <div>
            <dt>Mode</dt>
            <dd>{activeRun.mode}</dd>
          </div>
          <div>
            <dt>Seed</dt>
            <dd>{activeRun.seed}</dd>
          </div>
          <div>
            <dt>Working SRID</dt>
            <dd>EPSG:{activeRun.working_srid}</dd>
          </div>
        </dl>
      )}

      <div
        className={
          'infrastructure-state infrastructure-state-' + readModelStatus
        }
      >
        <strong>
          Read model: {readModelStatus.replace('-', ' ')}
        </strong>
        <span>{readModelMessage}</span>
        {(readModelStatus === 'not-ready' ||
          readModelStatus === 'error') && (
          <button
            className="button"
            type="button"
            onClick={() =>
              setReadModelRefreshNonce((value) => value + 1)
            }
            disabled={!runId}
          >
            Повторить проверку
          </button>
        )}
      </div>

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
            {layerCount(
              viewportLayers.existing,
              activeRun?.existing_facility_count ?? 0,
            )}
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
            {layerCount(
              viewportLayers.generated,
              activeRun?.generated_facility_count ?? 0,
            )}
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
            {layerCount(viewportLayers.demand)}
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
        disabled={!runId || viewportStatus === 'loading'}
      >
        Обновить viewport
      </button>

      <div
        className={'load-state load-state-' + viewportStatus}
      >
        {viewportMessage}
      </div>

      {layerErrors.length > 0 && (
        <div className="infrastructure-layer-errors">
          {layerErrors.map((item) => (
            <div key={item.key}>
              <strong>{item.label}</strong>
              <span>{item.message}</span>
            </div>
          ))}
          <button
            className="button"
            type="button"
            onClick={() => void loadViewport()}
          >
            Повторить viewport
          </button>
        </div>
      )}

      {truncatedLayers.length > 0 && (
        <p className="warning-text">
          Viewport limit ({VIEWPORT_LIMIT}) достигнут: {truncatedLayers.join(', ')}.
          Приблизьте карту, чтобы получить полный локальный набор.
        </p>
      )}

      <div className="infrastructure-authority-note">
        <strong>Authoritative read boundary</strong>
        <span>
          UI отображает только persisted backend results. Ошибки, 409 read-model
          readiness и truncation не запускают fallback-вычисления в браузере:
          frontend не выполняет snapping, routing, placement или metric
          computation.
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
                <dd>
                  {formatNumber(
                    numberProperty(demandProperties, 'gross_demand'),
                  )}
                </dd>
              </div>
              <div>
                <dt>Served demand</dt>
                <dd>
                  {formatNumber(
                    numberProperty(demandProperties, 'served_demand'),
                  )}
                </dd>
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
