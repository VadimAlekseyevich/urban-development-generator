import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import maplibregl, { type GeoJSONSource, type Map as MapLibreMap, type MapGeoJSONFeature } from 'maplibre-gl'

import {
  EMPTY_FEATURE_COLLECTION,
  SOURCE_LAYER_API_NAMES,
  SOURCE_LAYER_CONFIG,
  bboxParam,
  featureCollectionBounds,
  featureCollectionOf,
  featureTitle,
  geometryBounds,
  isUuid,
  mergeBounds,
  printableProperty,
  viewportBounds,
  type Bounds,
  type GeoJsonFeature,
  type GeoJsonFeatureCollection,
  type GeoJsonGeometry,
  type ProjectBoundaryResponse,
  type SourceLayerApiName,
  type SourceLayerKey,
  type SourceLayerResponse,
} from './sourceLayers'

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000/api/v1'
const VIEWPORT_LIMIT = 1500

const SOURCE_IDS: Record<SourceLayerKey, string> = {
  boundary: 'source-boundary',
  roads: 'source-roads',
  buildings: 'source-buildings',
  water: 'source-water',
  landuse: 'source-landuse',
}

const MAP_LAYER_IDS: Record<SourceLayerKey, readonly string[]> = {
  boundary: ['source-boundary-fill', 'source-boundary-line'],
  landuse: ['source-landuse-fill', 'source-landuse-line'],
  water: ['source-water-fill', 'source-water-line'],
  buildings: ['source-buildings-fill', 'source-buildings-line'],
  roads: ['source-roads-line'],
}

const MAP_LAYER_TO_SOURCE_KEY: Record<string, SourceLayerKey> = Object.fromEntries(
  Object.entries(MAP_LAYER_IDS).flatMap(([key, layerIds]) =>
    layerIds.map((layerId) => [layerId, key as SourceLayerKey]),
  ),
) as Record<string, SourceLayerKey>

const INITIAL_VISIBILITY: Record<SourceLayerKey, boolean> = {
  boundary: true,
  roads: true,
  buildings: true,
  water: true,
  landuse: true,
}

type ApiStatus = 'checking' | 'online' | 'offline'
type LoadStatus = 'idle' | 'loading' | 'ready' | 'error'
type SourceContext = { projectId: string; datasetVersionId: string }
type SelectedFeature = { layer: SourceLayerKey; feature: GeoJsonFeature }

type LayerStat = {
  count: number
  truncated: boolean
  error: string | null
}

type LayerStats = Record<SourceLayerApiName, LayerStat>

const EMPTY_STATS: LayerStats = {
  roads: { count: 0, truncated: false, error: null },
  buildings: { count: 0, truncated: false, error: null },
  water: { count: 0, truncated: false, error: null },
  landuse: { count: 0, truncated: false, error: null },
}

function initialParam(name: string): string {
  return new URLSearchParams(window.location.search).get(name) ?? ''
}

function makeContext(projectId: string, datasetVersionId: string): SourceContext | null {
  const project = projectId.trim()
  const version = datasetVersionId.trim()
  if (!isUuid(project) || !isUuid(version)) return null
  return { projectId: project, datasetVersionId: version }
}

function sourceData(map: MapLibreMap, key: SourceLayerKey, data: GeoJsonFeatureCollection): void {
  const source = map.getSource(SOURCE_IDS[key]) as GeoJSONSource | undefined
  source?.setData(data)
}

function mapFeature(feature: MapGeoJSONFeature): GeoJsonFeature {
  const properties = feature.properties ?? {}
  const fallbackId = properties.source_feature_id ?? properties.name ?? feature.layer.id
  return {
    type: 'Feature',
    id: String(feature.id ?? fallbackId),
    geometry: feature.geometry as GeoJsonGeometry,
    properties: { ...properties },
  }
}

function fitMap(map: MapLibreMap, bounds: Bounds): void {
  const [west, south, east, north] = bounds
  if (west === east && south === north) {
    map.easeTo({ center: [west, south], zoom: 16 })
    return
  }
  map.fitBounds(
    [
      [west, south],
      [east, north],
    ],
    { padding: 56, maxZoom: 16, duration: 450 },
  )
}

function App() {
  const mapContainer = useRef<HTMLDivElement | null>(null)
  const mapRef = useRef<MapLibreMap | null>(null)
  const viewportAbortRef = useRef<AbortController | null>(null)
  const boundaryAbortRef = useRef<AbortController | null>(null)
  const boundaryRef = useRef<ProjectBoundaryResponse | null>(null)
  const collectionsRef = useRef<Record<SourceLayerApiName, GeoJsonFeatureCollection>>({
    roads: EMPTY_FEATURE_COLLECTION,
    buildings: EMPTY_FEATURE_COLLECTION,
    water: EMPTY_FEATURE_COLLECTION,
    landuse: EMPTY_FEATURE_COLLECTION,
  })

  const initialProjectId = initialParam('project_id')
  const initialDatasetVersionId = initialParam('dataset_version_id')
  const [projectId, setProjectId] = useState(initialProjectId)
  const [datasetVersionId, setDatasetVersionId] = useState(initialDatasetVersionId)
  const [context, setContext] = useState<SourceContext | null>(() =>
    makeContext(initialProjectId, initialDatasetVersionId),
  )
  const [contextError, setContextError] = useState<string | null>(null)
  const [apiStatus, setApiStatus] = useState<ApiStatus>('checking')
  const [mapReady, setMapReady] = useState(false)
  const [loadStatus, setLoadStatus] = useState<LoadStatus>('idle')
  const [loadMessage, setLoadMessage] = useState('Укажите Project ID и DatasetVersion ID.')
  const [visibility, setVisibility] = useState(INITIAL_VISIBILITY)
  const [layerStats, setLayerStats] = useState<LayerStats>(EMPTY_STATS)
  const [boundaryAvailable, setBoundaryAvailable] = useState(false)
  const [selected, setSelected] = useState<SelectedFeature | null>(null)

  const activeLayerCount = useMemo(
    () => SOURCE_LAYER_API_NAMES.filter((layer) => visibility[layer]).length,
    [visibility],
  )

  useEffect(() => {
    fetch(`${API_BASE}/health/live`)
      .then((response) => {
        if (!response.ok) throw new Error('API unavailable')
        setApiStatus('online')
      })
      .catch(() => setApiStatus('offline'))
  }, [])

  useEffect(() => {
    if (!mapContainer.current) return

    const map = new maplibregl.Map({
      container: mapContainer.current,
      style: 'https://demotiles.maplibre.org/style.json',
      center: [37.6176, 55.7558],
      zoom: 9,
      renderWorldCopies: false,
      maxBounds: [
        [-180, -85],
        [180, 85],
      ],
    })
    mapRef.current = map
    map.addControl(new maplibregl.NavigationControl(), 'top-right')
    map.addControl(new maplibregl.ScaleControl({ unit: 'metric' }), 'bottom-right')

    map.on('load', () => {
      for (const key of Object.keys(SOURCE_IDS) as SourceLayerKey[]) {
        map.addSource(SOURCE_IDS[key], {
          type: 'geojson',
          data: EMPTY_FEATURE_COLLECTION,
        })
      }

      map.addLayer({
        id: 'source-boundary-fill',
        type: 'fill',
        source: SOURCE_IDS.boundary,
        paint: { 'fill-color': '#f59e0b', 'fill-opacity': 0.06 },
      })
      map.addLayer({
        id: 'source-boundary-line',
        type: 'line',
        source: SOURCE_IDS.boundary,
        paint: {
          'line-color': '#f59e0b',
          'line-width': 3,
          'line-dasharray': [2, 2],
        },
      })
      map.addLayer({
        id: 'source-landuse-fill',
        type: 'fill',
        source: SOURCE_IDS.landuse,
        paint: { 'fill-color': '#65a30d', 'fill-opacity': 0.22 },
      })
      map.addLayer({
        id: 'source-landuse-line',
        type: 'line',
        source: SOURCE_IDS.landuse,
        paint: { 'line-color': '#4d7c0f', 'line-width': 1 },
      })
      map.addLayer({
        id: 'source-water-fill',
        type: 'fill',
        source: SOURCE_IDS.water,
        paint: { 'fill-color': '#0ea5e9', 'fill-opacity': 0.5 },
        filter: ['==', ['geometry-type'], 'Polygon'],
      })
      map.addLayer({
        id: 'source-water-line',
        type: 'line',
        source: SOURCE_IDS.water,
        paint: { 'line-color': '#0284c7', 'line-width': 2 },
      })
      map.addLayer({
        id: 'source-buildings-fill',
        type: 'fill',
        source: SOURCE_IDS.buildings,
        paint: { 'fill-color': '#d97706', 'fill-opacity': 0.52 },
      })
      map.addLayer({
        id: 'source-buildings-line',
        type: 'line',
        source: SOURCE_IDS.buildings,
        paint: { 'line-color': '#92400e', 'line-width': 0.8 },
      })
      map.addLayer({
        id: 'source-roads-line',
        type: 'line',
        source: SOURCE_IDS.roads,
        paint: {
          'line-color': '#334155',
          'line-width': ['interpolate', ['linear'], ['zoom'], 7, 1, 12, 2, 16, 4],
        },
      })
      setMapReady(true)
    })

    map.on('click', (event) => {
      const interactiveLayers = Object.keys(MAP_LAYER_TO_SOURCE_KEY).filter((id) => map.getLayer(id))
      const features = map.queryRenderedFeatures(event.point, { layers: interactiveLayers })
      const first = features[0]
      if (!first) {
        setSelected(null)
        return
      }
      const layer = MAP_LAYER_TO_SOURCE_KEY[first.layer.id]
      if (!layer) return
      setSelected({ layer, feature: mapFeature(first) })
    })

    map.on('mousemove', (event) => {
      const interactiveLayers = Object.keys(MAP_LAYER_TO_SOURCE_KEY).filter((id) => map.getLayer(id))
      const hit = map.queryRenderedFeatures(event.point, { layers: interactiveLayers }).length > 0
      map.getCanvas().style.cursor = hit ? 'pointer' : ''
    })

    return () => {
      viewportAbortRef.current?.abort()
      boundaryAbortRef.current?.abort()
      map.remove()
      mapRef.current = null
    }
  }, [])

  useEffect(() => {
    const map = mapRef.current
    if (!map || !mapReady) return
    for (const config of SOURCE_LAYER_CONFIG) {
      for (const layerId of MAP_LAYER_IDS[config.key]) {
        if (!map.getLayer(layerId)) continue
        map.setLayoutProperty(layerId, 'visibility', visibility[config.key] ? 'visible' : 'none')
      }
    }
  }, [mapReady, visibility])

  const loadViewport = useCallback(async () => {
    const map = mapRef.current
    if (!map || !mapReady || !context) return

    viewportAbortRef.current?.abort()
    const controller = new AbortController()
    viewportAbortRef.current = controller

    const bounds = map.getBounds()
    const bbox = bboxParam(
      viewportBounds(bounds.getWest(), bounds.getSouth(), bounds.getEast(), bounds.getNorth()),
    )
    const requestedLayers = SOURCE_LAYER_API_NAMES.filter((layer) => visibility[layer])

    if (requestedLayers.length === 0) {
      setLoadStatus('ready')
      setLoadMessage('Все source layers скрыты.')
      return
    }

    setLoadStatus('loading')
    setLoadMessage(`Загружаю ${requestedLayers.length} слоёв для текущего viewport…`)

    const results = await Promise.allSettled(
      requestedLayers.map(async (layer) => {
        const path =
          `${API_BASE}/projects/${encodeURIComponent(context.projectId)}` +
          `/dataset-versions/${encodeURIComponent(context.datasetVersionId)}` +
          `/source-layers/${layer}/geojson` +
          `?bbox=${encodeURIComponent(bbox)}&limit=${VIEWPORT_LIMIT}`
        const response = await fetch(path, { signal: controller.signal })
        if (!response.ok) {
          const detail = await response.text()
          throw new Error(`${layer}: HTTP ${response.status} ${detail}`)
        }
        return (await response.json()) as SourceLayerResponse
      }),
    )

    if (controller.signal.aborted) return

    const updates: Partial<LayerStats> = {}
    let failed = 0
    let total = 0

    results.forEach((result, index) => {
      const layer = requestedLayers[index]
      if (result.status === 'fulfilled') {
        const collection: GeoJsonFeatureCollection = {
          type: 'FeatureCollection',
          features: result.value.features,
        }
        collectionsRef.current[layer] = collection
        sourceData(map, layer, collection)
        updates[layer] = {
          count: result.value.features.length,
          truncated: result.value.truncated,
          error: null,
        }
        total += result.value.features.length
        return
      }
      failed += 1
      updates[layer] = {
        count: 0,
        truncated: false,
        error: result.reason instanceof Error ? result.reason.message : String(result.reason),
      }
    })

    setLayerStats((current) => ({ ...current, ...updates }))
    if (failed > 0) {
      setLoadStatus('error')
      setLoadMessage(`Загружено объектов: ${total}. Ошибок слоёв: ${failed}.`)
    } else {
      setLoadStatus('ready')
      setLoadMessage(`В viewport загружено объектов: ${total}.`)
    }
  }, [context, mapReady, visibility])

  useEffect(() => {
    const map = mapRef.current
    if (!map || !mapReady || !context) return
    const handleMoveEnd = () => void loadViewport()
    map.on('moveend', handleMoveEnd)
    void loadViewport()
    return () => map.off('moveend', handleMoveEnd)
  }, [context, loadViewport, mapReady])

  useEffect(() => {
    const map = mapRef.current
    if (!map || !mapReady) return

    boundaryAbortRef.current?.abort()
    boundaryRef.current = null
    setBoundaryAvailable(false)
    sourceData(map, 'boundary', EMPTY_FEATURE_COLLECTION)

    if (!context) return

    const controller = new AbortController()
    boundaryAbortRef.current = controller
    const path = `${API_BASE}/projects/${encodeURIComponent(context.projectId)}/boundary/geojson`

    void fetch(path, { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error(`boundary: HTTP ${response.status} ${await response.text()}`)
        return (await response.json()) as ProjectBoundaryResponse
      })
      .then((boundary) => {
        if (controller.signal.aborted) return
        boundaryRef.current = boundary
        setBoundaryAvailable(boundary.geometry !== null)
        sourceData(map, 'boundary', featureCollectionOf(boundary.geometry ? boundary : null))
        if (boundary.geometry) {
          const bounds = geometryBounds(boundary.geometry)
          if (bounds) fitMap(map, bounds)
        }
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return
        setBoundaryAvailable(false)
        setLoadStatus('error')
        setLoadMessage(error instanceof Error ? error.message : String(error))
      })

    return () => controller.abort()
  }, [context, mapReady])

  function applyContext(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault()
    const next = makeContext(projectId, datasetVersionId)
    if (!next) {
      setContextError('Оба идентификатора должны быть UUID.')
      return
    }

    setContextError(null)
    setSelected(null)
    setLayerStats(EMPTY_STATS)
    setContext(next)
    const url = new URL(window.location.href)
    url.searchParams.set('project_id', next.projectId)
    url.searchParams.set('dataset_version_id', next.datasetVersionId)
    window.history.replaceState({}, '', url)
  }

  function toggleLayer(layer: SourceLayerKey): void {
    setVisibility((current) => ({ ...current, [layer]: !current[layer] }))
  }

  function fitVisibleData(): void {
    const map = mapRef.current
    if (!map) return

    let bounds: Bounds | null = null
    if (visibility.boundary && boundaryRef.current?.geometry) {
      bounds = geometryBounds(boundaryRef.current.geometry)
    }
    for (const layer of SOURCE_LAYER_API_NAMES) {
      if (!visibility[layer]) continue
      bounds = mergeBounds(bounds, featureCollectionBounds(collectionsRef.current[layer]))
    }
    if (bounds) fitMap(map, bounds)
  }

  return (
    <main className="layout">
      <aside className="sidebar">
        <header className="brand">
          <p className="eyebrow">Urban Development Generator</p>
          <h1>Source layers</h1>
          <p className="muted">Визуальная проверка нормализованных исходных данных</p>
        </header>

        <div className={`status status-${apiStatus}`}>
          <span className="status-dot" /> API: {apiStatus}
        </div>

        <section className="panel">
          <div className="section-heading">
            <div>
              <p className="section-kicker">Контекст</p>
              <h2>Проект и версия данных</h2>
            </div>
          </div>
          <form className="context-form" onSubmit={applyContext}>
            <label>
              <span>Project ID</span>
              <input
                value={projectId}
                onChange={(event) => setProjectId(event.target.value)}
                placeholder="UUID проекта"
                autoComplete="off"
              />
            </label>
            <label>
              <span>DatasetVersion ID</span>
              <input
                value={datasetVersionId}
                onChange={(event) => setDatasetVersionId(event.target.value)}
                placeholder="UUID версии"
                autoComplete="off"
              />
            </label>
            {contextError && <p className="form-error">{contextError}</p>}
            <button className="button button-primary" type="submit">
              Открыть данные
            </button>
          </form>
          <p className="helper-text">Контекст сохраняется в URL и подходит для повторной проверки.</p>
        </section>

        <section className="panel">
          <div className="section-heading section-heading-row">
            <div>
              <p className="section-kicker">Легенда</p>
              <h2>Исходные слои</h2>
            </div>
            <span className="badge">{activeLayerCount}/4</span>
          </div>
          <div className="layer-list">
            {SOURCE_LAYER_CONFIG.map((layer) => {
              const stat = layer.key === 'boundary' ? null : layerStats[layer.key]
              return (
                <label className="layer-row" key={layer.key}>
                  <input
                    type="checkbox"
                    checked={visibility[layer.key]}
                    onChange={() => toggleLayer(layer.key)}
                  />
                  <span className="swatch" style={{ backgroundColor: layer.color }} />
                  <span className="layer-label">{layer.label}</span>
                  <span className="layer-count">
                    {layer.key === 'boundary'
                      ? boundaryAvailable
                        ? '1'
                        : '—'
                      : stat?.truncated
                        ? `${stat.count}+`
                        : String(stat?.count ?? 0)}
                  </span>
                </label>
              )
            })}
          </div>
          <div className="button-row">
            <button className="button" type="button" onClick={fitVisibleData} disabled={!context}>
              Fit to data
            </button>
            <button className="button" type="button" onClick={() => void loadViewport()} disabled={!context}>
              Обновить
            </button>
          </div>
          <div className={`load-state load-state-${loadStatus}`}>{loadMessage}</div>
          {Object.entries(layerStats).some(([, stat]) => stat.truncated) && (
            <p className="warning-text">
              Один или несколько слоёв достигли viewport limit ({VIEWPORT_LIMIT}); приблизьте карту.
            </p>
          )}
        </section>

        <section className="panel inspector-panel">
          <div className="section-heading">
            <div>
              <p className="section-kicker">Inspector</p>
              <h2>{selected ? featureTitle(selected.layer, selected.feature) : 'Выберите объект на карте'}</h2>
            </div>
          </div>
          {selected ? (
            <dl className="property-grid">
              <div>
                <dt>Layer</dt>
                <dd>{selected.layer}</dd>
              </div>
              <div>
                <dt>Feature ID</dt>
                <dd>{selected.feature.id}</dd>
              </div>
              {Object.entries(selected.feature.properties)
                .filter(([, value]) => value !== null && value !== undefined)
                .slice(0, 12)
                .map(([key, value]) => (
                  <div key={key}>
                    <dt>{key}</dt>
                    <dd>{printableProperty(value)}</dd>
                  </div>
                ))}
            </dl>
          ) : (
            <p className="helper-text">Кликните по дороге, зданию, воде, landuse или границе проекта.</p>
          )}
        </section>
      </aside>

      <section className="map-shell" aria-label="Карта исходных слоёв">
        <div className="map-overlay">
          <span className={`map-state map-state-${loadStatus}`}>
            {context ? context.datasetVersionId.slice(0, 8) : 'no dataset'}
          </span>
        </div>
        <div ref={mapContainer} className="map" />
      </section>
    </main>
  )
}

export default App
