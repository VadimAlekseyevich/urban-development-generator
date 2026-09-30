import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ChangeEvent,
  type FormEvent,
} from 'react'
import maplibregl, {
  type GeoJSONSource,
  type Map as MapLibreMap,
  type MapGeoJSONFeature,
} from 'maplibre-gl'

import { BlockParcelsPanel } from './BlockParcelsPanel'
import { ComparePanel } from './ComparePanel'
import { BuildingsPanel } from './BuildingsPanel'
import { DemographyPanel } from './DemographyPanel'
import { InfrastructurePanel } from './InfrastructurePanel'
import { LayerTree } from './LayerTree'
import { MetricsDashboard } from './MetricsDashboard'
import { RoadsPanel } from './RoadsPanel'
import { RunsPanel } from './RunsPanel'
import { SuitabilityPanel } from './SuitabilityPanel'
import { ViolationsPanel } from './ViolationsPanel'
import { ZoningPanel } from './ZoningPanel'
import {
  EMPTY_FEATURE_COLLECTION,
  SOURCE_LAYER_API_NAMES,
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
import {
  catalogReadUrl,
  layerDefinition,
  layerInstancesForContext,
  legacyBboxUrl,
  type LayerId,
} from './layerRegistry'
import { SOURCE_MAP_LEGEND } from './layerStyles'
import { installCatalogGeoJson, layerStyleIds } from './mapLayerAdapter'
import { useLayerVisibilitySnapshot } from './layerVisibility'

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000/api/v1'
const VIEWPORT_LIMIT = 1500

const SOURCE_LAYER_IDS: Record<SourceLayerKey, LayerId> = Object.fromEntries(
  SOURCE_MAP_LEGEND.map(({ key, layerId }) => [key, layerId]),
) as Record<SourceLayerKey, LayerId>
const SOURCE_IDS: Record<SourceLayerKey, string> = Object.fromEntries(
  SOURCE_MAP_LEGEND.map(({ key, layerId }) => [key, layerDefinition(layerId).sourceId]),
) as Record<SourceLayerKey, string>
const MAP_LAYER_IDS: Record<SourceLayerKey, readonly string[]> = Object.fromEntries(
  SOURCE_MAP_LEGEND.map(({ key, layerId }) => [key, layerStyleIds(layerId)]),
) as Record<SourceLayerKey, readonly string[]>
const MAP_LAYER_TO_SOURCE: Record<string, SourceLayerKey> = Object.fromEntries(
  SOURCE_MAP_LEGEND.flatMap(({ key, layerId }) =>
    layerStyleIds(layerId).map((id) => [id, key]),
  ),
) as Record<string, SourceLayerKey>
type ApiStatus = 'checking' | 'online' | 'offline'
type LoadStatus = 'idle' | 'loading' | 'ready' | 'error'
type SourceContext = { projectId: string; datasetVersionId: string }
type SelectedFeature = { layer: SourceLayerKey; feature: GeoJsonFeature }
type LayerStat = { count: number; truncated: boolean; error: string | null }
type LayerStats = Record<SourceLayerApiName, LayerStat>

const EMPTY_STATS: LayerStats = {
  roads: { count: 0, truncated: false, error: null },
  buildings: { count: 0, truncated: false, error: null },
  water: { count: 0, truncated: false, error: null },
  landuse: { count: 0, truncated: false, error: null },
  facilities: { count: 0, truncated: false, error: null },
  constraints: { count: 0, truncated: false, error: null },
}

function initialParam(name: string): string {
  return new URLSearchParams(window.location.search).get(name) ?? ''
}

function parseContext(projectId: string, datasetVersionId: string): SourceContext | null {
  const project = projectId.trim()
  const version = datasetVersionId.trim()
  if (!isUuid(project) || !isUuid(version)) return null
  return { projectId: project, datasetVersionId: version }
}

function setSourceData(
  map: MapLibreMap,
  layer: SourceLayerKey,
  data: GeoJsonFeatureCollection,
): void {
  const source = map.getSource(SOURCE_IDS[layer]) as GeoJSONSource | undefined
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

function addSourceLayers(map: MapLibreMap): void {
  for (const { layerId } of SOURCE_MAP_LEGEND) installCatalogGeoJson(map, layerId)
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
    facilities: EMPTY_FEATURE_COLLECTION,
    constraints: EMPTY_FEATURE_COLLECTION,
  })

  const initialProjectId = initialParam('project_id')
  const initialVersionId = initialParam('dataset_version_id')
  const initialSuitabilityArtifactId = initialParam('suitability_artifact_id')
  const [projectId, setProjectId] = useState(initialProjectId)
  const [datasetVersionId, setDatasetVersionId] = useState(initialVersionId)
  const [context, setContext] = useState<SourceContext | null>(() =>
    parseContext(initialProjectId, initialVersionId),
  )
  const [contextError, setContextError] = useState<string | null>(null)
  const [apiStatus, setApiStatus] = useState<ApiStatus>('checking')
  const [mapReady, setMapReady] = useState(false)
  const [loadStatus, setLoadStatus] = useState<LoadStatus>('idle')
  const [loadMessage, setLoadMessage] = useState('Укажите Project ID и DatasetVersion ID.')
  const visibility = useLayerVisibilitySnapshot()
  const [layerStats, setLayerStats] = useState<LayerStats>(EMPTY_STATS)
  const [boundaryAvailable, setBoundaryAvailable] = useState(false)
  const [selected, setSelected] = useState<SelectedFeature | null>(null)
  const [mapRunId, setMapRunId] = useState<string | null>(null)
  const [suitabilityArtifactId, setSuitabilityArtifactId] = useState<string | null>(
    isUuid(initialSuitabilityArtifactId) ? initialSuitabilityArtifactId : null,
  )

  useEffect(() => {
    void fetch(`${API_BASE}/health/live`)
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
      // Do not use full-world maxBounds here. A 360-degree longitude span can make
      // MapLibre's projection matrix singular during resize (maplibre-gl-js#6148).
    })
    mapRef.current = map
    map.addControl(new maplibregl.NavigationControl(), 'top-right')
    map.addControl(new maplibregl.ScaleControl({ unit: 'metric' }), 'bottom-right')

    map.on('load', () => {
      addSourceLayers(map)
      setMapReady(true)
    })

    map.on('click', (event) => {
      const layers = Object.keys(MAP_LAYER_TO_SOURCE).filter((id) => map.getLayer(id))
      const feature = map.queryRenderedFeatures(event.point, { layers })[0]
      if (!feature) {
        setSelected(null)
        return
      }
      const layer = MAP_LAYER_TO_SOURCE[feature.layer.id]
      if (layer) setSelected({ layer, feature: mapFeature(feature) })
    })

    map.on('mousemove', (event) => {
      const layers = Object.keys(MAP_LAYER_TO_SOURCE).filter((id) => map.getLayer(id))
      const hasHit = map.queryRenderedFeatures(event.point, { layers }).length > 0
      map.getCanvas().style.cursor = hasHit ? 'pointer' : ''
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
    for (const config of SOURCE_MAP_LEGEND) {
      for (const layerId of MAP_LAYER_IDS[config.key]) {
        if (!map.getLayer(layerId)) continue
        map.setLayoutProperty(
          layerId,
          'visibility',
          visibility[config.layerId] ? 'visible' : 'none',
        )
      }
    }
  }, [mapReady, visibility])

  const loadViewport = useCallback(async () => {
    const map = mapRef.current
    if (!map || !mapReady || !context) return

    viewportAbortRef.current?.abort()
    const controller = new AbortController()
    viewportAbortRef.current = controller

    const mapBounds = map.getBounds()
    const bounds = viewportBounds(
      mapBounds.getWest(), mapBounds.getSouth(), mapBounds.getEast(), mapBounds.getNorth(),
    )
    const catalog = layerInstancesForContext({
      projectId: context.projectId, datasetVersionId: context.datasetVersionId,
    })
    const requested = SOURCE_LAYER_API_NAMES.filter((layer) => visibility[SOURCE_LAYER_IDS[layer]])
    if (requested.length === 0) {
      setLoadStatus('ready')
      setLoadMessage('Все source layers скрыты.')
      return
    }

    setLoadStatus('loading')
    setLoadMessage(`Загружаю ${requested.length} слоёв для текущего viewport…`)

    const results = await Promise.allSettled(
      requested.map(async (layer) => {
        const entry = catalog.find((item) => item.definition.id === SOURCE_LAYER_IDS[layer])
        if (!entry) throw new Error(`Missing catalog owner for ${layer}`)
        const url = legacyBboxUrl(API_BASE, entry, bounds, VIEWPORT_LIMIT)
        const response = await fetch(url, { signal: controller.signal })
        if (!response.ok) {
          throw new Error(`${layer}: HTTP ${response.status} ${await response.text()}`)
        }
        return (await response.json()) as SourceLayerResponse
      }),
    )
    if (controller.signal.aborted) return

    const updates: Partial<LayerStats> = {}
    let failures = 0
    let total = 0

    results.forEach((result, index) => {
      const layer = requested[index]
      if (result.status === 'rejected') {
        failures += 1
        updates[layer] = {
          count: 0,
          truncated: false,
          error: result.reason instanceof Error ? result.reason.message : String(result.reason),
        }
        return
      }

      const collection: GeoJsonFeatureCollection = {
        type: 'FeatureCollection',
        features: result.value.features,
      }
      collectionsRef.current[layer] = collection
      setSourceData(map, layer, collection)
      updates[layer] = {
        count: result.value.features.length,
        truncated: result.value.truncated,
        error: null,
      }
      total += result.value.features.length
    })

    setLayerStats((current) => ({ ...current, ...updates }))
    setLoadStatus(failures ? 'error' : 'ready')
    setLoadMessage(
      failures
        ? `Загружено объектов: ${total}. Ошибок слоёв: ${failures}.`
        : `В viewport загружено объектов: ${total}.`,
    )
  }, [context, mapReady, visibility])

  useEffect(() => {
    const map = mapRef.current
    if (!map || !mapReady || !context) return
    const handleMoveEnd = () => void loadViewport()
    map.on('moveend', handleMoveEnd)
    void loadViewport()
    return () => {
      map.off('moveend', handleMoveEnd)
    }
  }, [context, loadViewport, mapReady])

  useEffect(() => {
    const map = mapRef.current
    if (!map || !mapReady) return

    boundaryAbortRef.current?.abort()
    boundaryRef.current = null
    setBoundaryAvailable(false)
    setSourceData(map, 'boundary', EMPTY_FEATURE_COLLECTION)
    if (!context) return

    const controller = new AbortController()
    boundaryAbortRef.current = controller
    const entry = layerInstancesForContext({ projectId: context.projectId })
      .find((item) => item.definition.id === 'project.boundary')
    if (!entry) return
    const url = catalogReadUrl(API_BASE, entry)

    void fetch(url, { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) {
          throw new Error(`boundary: HTTP ${response.status} ${await response.text()}`)
        }
        return (await response.json()) as ProjectBoundaryResponse
      })
      .then((boundary) => {
        if (controller.signal.aborted) return
        boundaryRef.current = boundary
        setBoundaryAvailable(boundary.geometry !== null)
        const feature: GeoJsonFeature | null = boundary.geometry
          ? {
              type: 'Feature',
              id: boundary.id,
              geometry: boundary.geometry,
              properties: boundary.properties,
            }
          : null
        setSourceData(map, 'boundary', featureCollectionOf(feature))
        const bounds = geometryBounds(boundary.geometry)
        if (bounds) fitMap(map, bounds)
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return
        setBoundaryAvailable(false)
        setLoadStatus('error')
        setLoadMessage(error instanceof Error ? error.message : String(error))
      })

    return () => {
      controller.abort()
    }
  }, [context, mapReady])

  function applyContext(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault()
    const next = parseContext(projectId, datasetVersionId)
    if (!next) {
      setContextError('Оба идентификатора должны быть UUID.')
      return
    }

    setContextError(null)
    setSelected(null)
    setMapRunId(null)
    setLayerStats(EMPTY_STATS)
    setContext(next)
    const url = new URL(window.location.href)
    url.searchParams.set('project_id', next.projectId)
    url.searchParams.set('dataset_version_id', next.datasetVersionId)
    window.history.replaceState({}, '', url)
  }

  function updateProjectId(event: ChangeEvent<HTMLInputElement>): void {
    setProjectId(event.target.value)
  }

  function updateDatasetVersionId(event: ChangeEvent<HTMLInputElement>): void {
    setDatasetVersionId(event.target.value)
  }

  function fitVisibleData(): void {
    const map = mapRef.current
    if (!map) return

    let bounds: Bounds | null = null
    if (visibility['project.boundary'] && boundaryRef.current?.geometry) {
      bounds = geometryBounds(boundaryRef.current.geometry)
    }
    for (const layer of SOURCE_LAYER_API_NAMES) {
      if (!visibility[SOURCE_LAYER_IDS[layer]]) continue
      bounds = mergeBounds(bounds, featureCollectionBounds(collectionsRef.current[layer]))
    }
    if (bounds) fitMap(map, bounds)
  }

  const anyTruncated = Object.values(layerStats).some((stat) => stat.truncated)
  const layerCounts: Partial<Record<LayerId, string | number>> = {
    'project.boundary': boundaryAvailable ? 1 : '—',
  }
  for (const layer of SOURCE_LAYER_API_NAMES) {
    const stat = layerStats[layer]
    layerCounts[SOURCE_LAYER_IDS[layer]] = stat.truncated ? `${stat.count}+` : stat.count
  }

  return (
    <main className="layout">
      <aside className="sidebar">
        <header className="brand">
          <p className="eyebrow">Urban Development Generator</p>
          <h1>Source & suitability</h1>
          <p className="muted">Исходные слои и constraint-aware карта пригодности</p>
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
                onChange={updateProjectId}
                placeholder="UUID проекта"
                autoComplete="off"
              />
            </label>
            <label>
              <span>DatasetVersion ID</span>
              <input
                value={datasetVersionId}
                onChange={updateDatasetVersionId}
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

        <RunsPanel
          key={context?.projectId ?? 'no-project'}
          apiBase={API_BASE}
          projectId={context?.projectId ?? null}
          datasetVersionId={context?.datasetVersionId ?? null}
        />

        <ComparePanel
          key={context?.projectId ?? 'no-compare-project'}
          apiBase={API_BASE}
          projectId={context?.projectId ?? null}
          mapRunId={mapRunId}
          onMapRunChange={setMapRunId}
        />

        <LayerTree
          selection={{
            projectId: context?.projectId ?? null,
            datasetVersionId: context?.datasetVersionId ?? null,
            runId: mapRunId,
            suitabilityArtifactId,
          }}
          counts={layerCounts}
          sourceStatus={loadStatus}
          sourceMessage={loadMessage}
          sourceTruncated={anyTruncated}
          sourceLimit={VIEWPORT_LIMIT}
          onFitSource={fitVisibleData}
          onRefreshSource={() => void loadViewport()}
        />

        <SuitabilityPanel
          apiBase={API_BASE}
          map={mapReady ? mapRef.current : null}
          onArtifactChange={setSuitabilityArtifactId}
        />

        <ZoningPanel
          pinnedRunId={mapRunId}
          apiBase={API_BASE}
          map={mapReady ? mapRef.current : null}
          projectId={context?.projectId ?? null}
          datasetVersionId={context?.datasetVersionId ?? null}
        />

        <RoadsPanel
          pinnedRunId={mapRunId}
          apiBase={API_BASE}
          map={mapReady ? mapRef.current : null}
          projectId={context?.projectId ?? null}
          datasetVersionId={context?.datasetVersionId ?? null}
        />

        <BlockParcelsPanel
          pinnedRunId={mapRunId}
          apiBase={API_BASE}
          map={mapReady ? mapRef.current : null}
          projectId={context?.projectId ?? null}
        />

        <BuildingsPanel
          pinnedRunId={mapRunId}
          apiBase={API_BASE}
          map={mapReady ? mapRef.current : null}
          projectId={context?.projectId ?? null}
        />

        <DemographyPanel
          pinnedRunId={mapRunId}
          apiBase={API_BASE}
          map={mapReady ? mapRef.current : null}
          projectId={context?.projectId ?? null}
        />

        <InfrastructurePanel
          pinnedRunId={mapRunId}
          apiBase={API_BASE}
          map={mapReady ? mapRef.current : null}
          projectId={context?.projectId ?? null}
        />

        <MetricsDashboard
          apiBase={API_BASE}
          projectId={context?.projectId ?? null}
        />

        <ViolationsPanel
          pinnedRunId={mapRunId}
          apiBase={API_BASE}
          map={mapReady ? mapRef.current : null}
          projectId={context?.projectId ?? null}
        />

        <section className="panel inspector-panel">
          <div className="section-heading">
            <div>
              <p className="section-kicker">Inspector</p>
              <h2>
                {selected
                  ? featureTitle(selected.layer, selected.feature)
                  : 'Выберите объект на карте'}
              </h2>
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
            <p className="helper-text">
              Кликните по дороге, зданию, воде, landuse или границе проекта.
            </p>
          )}
        </section>
      </aside>

      <section className="map-shell" aria-label="Карта исходных слоёв и suitability">
        <div className="map-overlay">
          <span className={`map-state map-state-${loadStatus}`}>
            {mapRunId ? `run ${mapRunId.slice(0, 8)}` : context ? context.datasetVersionId.slice(0, 8) : 'no dataset'}
          </span>
        </div>
        <div ref={mapContainer} className="map" />
      </section>
    </main>
  )
}

export default App