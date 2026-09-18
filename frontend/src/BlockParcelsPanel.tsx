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
const BLOCK_SOURCE_ID = 'blocks-ui-source'
const PARCEL_SOURCE_ID = 'parcels-ui-source'
const BLOCK_FILL_ID = 'blocks-ui-fill'
const BLOCK_LINE_ID = 'blocks-ui-line'
const PARCEL_FILL_ID = 'parcels-ui-fill'
const PARCEL_LINE_ID = 'parcels-ui-line'

type LoadStatus = 'idle' | 'loading' | 'ready' | 'error'
type FeatureKind = 'block' | 'parcel'

type BlockParcelRunSummary = {
  id: string
  project_id: string
  status: string
  mode: string
  seed: number
  working_srid: number
  generated_block_count: number
  generated_parcel_count: number
  created_at: string
  finished_at: string | null
}

type BlockParcelResponse = GeoJsonFeatureCollection & {
  project_id: string
  run_id: string
  query_bbox: [number, number, number, number]
  geojson_crs: 'EPSG:4326'
  working_srid: number
  limit: number
  truncated: boolean
}

type SelectedFeature = {
  kind: FeatureKind
  feature: GeoJsonFeature
}

type BlockParcelsPanelProps = {
  apiBase: string
  map: MapLibreMap | null
  projectId: string | null
}

function setSourceData(
  map: MapLibreMap,
  sourceId: string,
  data: GeoJsonFeatureCollection,
): void {
  const source = map.getSource(sourceId) as GeoJSONSource | undefined
  source?.setData(data)
}

function ensureLayers(map: MapLibreMap): void {
  if (!map.getSource(BLOCK_SOURCE_ID)) {
    map.addSource(BLOCK_SOURCE_ID, {
      type: 'geojson',
      data: EMPTY_FEATURE_COLLECTION,
    })
  }
  if (!map.getSource(PARCEL_SOURCE_ID)) {
    map.addSource(PARCEL_SOURCE_ID, {
      type: 'geojson',
      data: EMPTY_FEATURE_COLLECTION,
    })
  }

  if (!map.getLayer(BLOCK_FILL_ID)) {
    map.addLayer({
      id: BLOCK_FILL_ID,
      type: 'fill',
      source: BLOCK_SOURCE_ID,
      paint: {
        'fill-color': [
          'match',
          ['get', 'association_status'],
          'ASSOCIATED',
          '#2563eb',
          'PARTIAL_OVERLAP',
          '#f59e0b',
          'AMBIGUOUS_FULL_COVERAGE',
          '#7c3aed',
          'NO_OVERLAP',
          '#dc2626',
          '#64748b',
        ],
        'fill-opacity': 0.18,
      },
    })
  }
  if (!map.getLayer(BLOCK_LINE_ID)) {
    map.addLayer({
      id: BLOCK_LINE_ID,
      type: 'line',
      source: BLOCK_SOURCE_ID,
      paint: {
        'line-color': [
          'match',
          ['get', 'association_status'],
          'ASSOCIATED',
          '#1d4ed8',
          'PARTIAL_OVERLAP',
          '#d97706',
          'AMBIGUOUS_FULL_COVERAGE',
          '#6d28d9',
          'NO_OVERLAP',
          '#b91c1c',
          '#475569',
        ],
        'line-width': ['interpolate', ['linear'], ['zoom'], 8, 0.8, 14, 1.6, 17, 2.2],
      },
    })
  }
  if (!map.getLayer(PARCEL_FILL_ID)) {
    map.addLayer({
      id: PARCEL_FILL_ID,
      type: 'fill',
      source: PARCEL_SOURCE_ID,
      paint: {
        'fill-color': [
          'case',
          ['==', ['get', 'has_frontage'], false],
          '#ef4444',
          '#22c55e',
        ],
        'fill-opacity': 0.09,
      },
    })
  }
  if (!map.getLayer(PARCEL_LINE_ID)) {
    map.addLayer({
      id: PARCEL_LINE_ID,
      type: 'line',
      source: PARCEL_SOURCE_ID,
      paint: {
        'line-color': [
          'case',
          ['==', ['get', 'has_frontage'], false],
          '#dc2626',
          '#15803d',
        ],
        'line-width': ['interpolate', ['linear'], ['zoom'], 10, 0.6, 15, 1.2, 18, 1.8],
      },
    })
  }
}

function formatRun(run: BlockParcelRunSummary): string {
  return (
    `${run.id.slice(0, 8)} · ${run.status} · ` +
    `${run.generated_block_count} blocks · ${run.generated_parcel_count} parcels`
  )
}

function mapFeature(feature: MapGeoJSONFeature): GeoJsonFeature {
  const properties = feature.properties ?? {}
  const fallback =
    properties.parcel_key ??
    properties.block_key ??
    feature.layer.id
  return {
    type: 'Feature',
    id: String(feature.id ?? fallback),
    geometry: feature.geometry as GeoJsonGeometry,
    properties: { ...properties },
  }
}

function asRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  return value as Record<string, unknown>
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

function booleanProperty(
  properties: Record<string, unknown>,
  key: string,
): boolean | null {
  const value = properties[key]
  return typeof value === 'boolean' ? value : null
}

function formatArea(areaM2: number | null): string {
  if (areaM2 === null) return '—'
  if (areaM2 >= 10_000) return `${(areaM2 / 10_000).toFixed(2)} ha`
  return `${areaM2.toFixed(0)} m²`
}

function formatLength(lengthM: number | null): string {
  if (lengthM === null) return '—'
  return `${lengthM.toFixed(1)} m`
}

function featureTitle(selected: SelectedFeature): string {
  const properties = selected.feature.properties
  return (
    textProperty(
      properties,
      selected.kind === 'block' ? 'block_key' : 'parcel_key',
    ) ?? `${selected.kind}: ${selected.feature.id}`
  )
}

function validationFlags(selected: SelectedFeature): string[] {
  const properties = selected.feature.properties
  const flags: string[] = []

  if (selected.kind === 'block') {
    const status = textProperty(properties, 'association_status')
    if (status && status !== 'ASSOCIATED') {
      flags.push(`Zone association: ${status}`)
    }
    const subdivision = asRecord(properties.subdivision)
    const skipReason = subdivision?.skip_reason
    if (typeof skipReason === 'string' && skipReason) {
      flags.push(`Subdivision: ${skipReason}`)
    }
  } else {
    if (booleanProperty(properties, 'has_frontage') === false) {
      flags.push('No persisted road frontage')
    }
    if (!textProperty(properties, 'zone_class')) {
      flags.push('No persisted zone association')
    }
  }

  return flags
}

export function BlockParcelsPanel({
  apiBase,
  map,
  projectId,
}: BlockParcelsPanelProps) {
  const abortRef = useRef<AbortController | null>(null)
  const [runs, setRuns] = useState<BlockParcelRunSummary[]>([])
  const [selectedRunId, setSelectedRunId] = useState('')
  const [blocksVisible, setBlocksVisible] = useState(true)
  const [parcelsVisible, setParcelsVisible] = useState(true)
  const [blockCount, setBlockCount] = useState(0)
  const [parcelCount, setParcelCount] = useState(0)
  const [blocksTruncated, setBlocksTruncated] = useState(false)
  const [parcelsTruncated, setParcelsTruncated] = useState(false)
  const [selected, setSelected] = useState<SelectedFeature | null>(null)
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
    for (const id of [BLOCK_FILL_ID, BLOCK_LINE_ID]) {
      map.setLayoutProperty(id, 'visibility', blocksVisible ? 'visible' : 'none')
    }
  }, [blocksVisible, map])

  useEffect(() => {
    if (!map) return
    ensureLayers(map)
    for (const id of [PARCEL_FILL_ID, PARCEL_LINE_ID]) {
      map.setLayoutProperty(id, 'visibility', parcelsVisible ? 'visible' : 'none')
    }
  }, [map, parcelsVisible])

  useEffect(() => {
    if (!map) return

    const handleClick = (event: maplibregl.MapMouseEvent): void => {
      const layerIds = [
        ...(parcelsVisible ? [PARCEL_FILL_ID, PARCEL_LINE_ID] : []),
        ...(blocksVisible ? [BLOCK_FILL_ID, BLOCK_LINE_ID] : []),
      ].filter((id) => map.getLayer(id))
      if (layerIds.length === 0) return

      const feature = map.queryRenderedFeatures(event.point, { layers: layerIds })[0]
      if (!feature) return
      const kind: FeatureKind = feature.layer.id.startsWith('parcels-ui')
        ? 'parcel'
        : 'block'
      setSelected({ kind, feature: mapFeature(feature) })
    }

    map.on('click', handleClick)
    return () => {
      map.off('click', handleClick)
    }
  }, [blocksVisible, map, parcelsVisible])

  useEffect(() => {
    if (!projectId) {
      setRuns([])
      setSelectedRunId('')
      setSelected(null)
      setBlockCount(0)
      setParcelCount(0)
      if (map) {
        ensureLayers(map)
        setSourceData(map, BLOCK_SOURCE_ID, EMPTY_FEATURE_COLLECTION)
        setSourceData(map, PARCEL_SOURCE_ID, EMPTY_FEATURE_COLLECTION)
      }
      return
    }

    const controller = new AbortController()
    void fetch(
      `${apiBase}/projects/${encodeURIComponent(projectId)}/block-runs`,
      { signal: controller.signal },
    )
      .then(async (response) => {
        if (!response.ok) {
          throw new Error(
            `block runs: HTTP ${response.status} ${await response.text()}`,
          )
        }
        return (await response.json()) as BlockParcelRunSummary[]
      })
      .then((nextRuns) => {
        if (controller.signal.aborted) return
        setRuns(nextRuns)
        const requested =
          new URLSearchParams(window.location.search).get('block_run_id') ?? ''
        setSelectedRunId((current) => {
          if (nextRuns.some((run) => run.id === requested)) return requested
          if (nextRuns.some((run) => run.id === current)) return current
          return (
            nextRuns.find(
              (run) =>
                run.generated_block_count > 0 || run.generated_parcel_count > 0,
            )?.id ??
            nextRuns[0]?.id ??
            ''
          )
        })
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return
        setRuns([])
        setSelectedRunId('')
        setSelected(null)
        setLoadStatus('error')
        setLoadMessage(error instanceof Error ? error.message : String(error))
      })

    return () => controller.abort()
  }, [apiBase, map, projectId])

  const loadViewport = useCallback(async () => {
    if (!map || !projectId || !selectedRunId) {
      setLoadStatus('idle')
      setLoadMessage(
        projectId ? 'Нет persisted block/parcel run.' : 'Выберите контекст проекта.',
      )
      return
    }

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

    if (!blocksVisible && !parcelsVisible) {
      setSourceData(map, BLOCK_SOURCE_ID, EMPTY_FEATURE_COLLECTION)
      setSourceData(map, PARCEL_SOURCE_ID, EMPTY_FEATURE_COLLECTION)
      setBlockCount(0)
      setParcelCount(0)
      setLoadStatus('ready')
      setLoadMessage('Blocks и parcels скрыты.')
      return
    }

    setLoadStatus('loading')
    setLoadMessage('Загружаю blocks/parcels для текущего viewport…')

    try {
      let nextBlockCount = 0
      let nextParcelCount = 0
      let nextBlocksTruncated = false
      let nextParcelsTruncated = false

      if (blocksVisible) {
        const response = await fetch(
          `${apiBase}/projects/${encodeURIComponent(projectId)}` +
            `/block-runs/${encodeURIComponent(selectedRunId)}/blocks/geojson` +
            `?bbox=${encodeURIComponent(bbox)}&limit=${VIEWPORT_LIMIT}`,
          { signal: controller.signal },
        )
        if (!response.ok) {
          throw new Error(
            `blocks: HTTP ${response.status} ${await response.text()}`,
          )
        }
        const blocks = (await response.json()) as BlockParcelResponse
        setSourceData(map, BLOCK_SOURCE_ID, blocks)
        nextBlockCount = blocks.features.length
        nextBlocksTruncated = blocks.truncated
      } else {
        setSourceData(map, BLOCK_SOURCE_ID, EMPTY_FEATURE_COLLECTION)
      }

      if (parcelsVisible) {
        const response = await fetch(
          `${apiBase}/projects/${encodeURIComponent(projectId)}` +
            `/block-runs/${encodeURIComponent(selectedRunId)}/parcels/geojson` +
            `?bbox=${encodeURIComponent(bbox)}&limit=${VIEWPORT_LIMIT}`,
          { signal: controller.signal },
        )
        if (!response.ok) {
          throw new Error(
            `parcels: HTTP ${response.status} ${await response.text()}`,
          )
        }
        const parcels = (await response.json()) as BlockParcelResponse
        setSourceData(map, PARCEL_SOURCE_ID, parcels)
        nextParcelCount = parcels.features.length
        nextParcelsTruncated = parcels.truncated
      } else {
        setSourceData(map, PARCEL_SOURCE_ID, EMPTY_FEATURE_COLLECTION)
      }

      if (controller.signal.aborted) return
      setBlockCount(nextBlockCount)
      setParcelCount(nextParcelCount)
      setBlocksTruncated(nextBlocksTruncated)
      setParcelsTruncated(nextParcelsTruncated)
      setLoadStatus('ready')
      setLoadMessage(
        `Viewport: blocks ${nextBlockCount}, parcels ${nextParcelCount}.`,
      )
    } catch (error: unknown) {
      if (controller.signal.aborted) return
      setLoadStatus('error')
      setLoadMessage(error instanceof Error ? error.message : String(error))
    }
  }, [
    apiBase,
    blocksVisible,
    map,
    parcelsVisible,
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
    setSelected(null)
    const url = new URL(window.location.href)
    if (runId) url.searchParams.set('block_run_id', runId)
    else url.searchParams.delete('block_run_id')
    window.history.replaceState({}, '', url)
  }

  const flags = selected ? validationFlags(selected) : []
  const properties = selected?.feature.properties ?? {}

  return (
    <section className="panel block-parcel-panel">
      <div className="section-heading section-heading-row">
        <div>
          <p className="section-kicker">S07 · Blocks & parcels</p>
          <h2>Urban fabric</h2>
        </div>
        <span className="badge">{runs.length} runs</span>
      </div>

      <label className="block-parcel-select">
        <span>Generated run</span>
        <select
          value={selectedRunId}
          onChange={changeRun}
          disabled={!projectId || runs.length === 0}
        >
          {runs.length === 0 && <option value="">Нет persisted block/parcel runs</option>}
          {runs.map((run) => (
            <option value={run.id} key={run.id}>
              {formatRun(run)}
            </option>
          ))}
        </select>
      </label>

      <div className="block-parcel-layer-list">
        <label className="block-parcel-layer-row">
          <input
            type="checkbox"
            checked={blocksVisible}
            onChange={(event) => setBlocksVisible(event.target.checked)}
          />
          <span className="swatch block-swatch" />
          <span>Generated blocks</span>
          <span className="layer-count">
            {blocksTruncated ? `${blockCount}+` : blockCount}
          </span>
        </label>
        <label className="block-parcel-layer-row">
          <input
            type="checkbox"
            checked={parcelsVisible}
            onChange={(event) => setParcelsVisible(event.target.checked)}
          />
          <span className="swatch parcel-swatch" />
          <span>Planning parcels</span>
          <span className="layer-count">
            {parcelsTruncated ? `${parcelCount}+` : parcelCount}
          </span>
        </label>
      </div>

      <div className="block-parcel-legend">
        <span><i className="legend-ok" /> associated/frontage</span>
        <span><i className="legend-warn" /> partial/ambiguous</span>
        <span><i className="legend-error" /> no overlap/frontage</span>
      </div>

      <div className={`load-state load-state-${loadStatus}`}>{loadMessage}</div>
      {(blocksTruncated || parcelsTruncated) && (
        <p className="warning-text">
          Block/parcel viewport достиг limit ({VIEWPORT_LIMIT}); приблизьте карту.
        </p>
      )}

      {selected ? (
        <div className="block-parcel-inspector">
          <div className="section-heading">
            <div>
              <p className="section-kicker">
                {selected.kind === 'block' ? 'Block inspector' : 'Parcel inspector'}
              </p>
              <h3>{featureTitle(selected)}</h3>
            </div>
          </div>

          <dl className="block-parcel-metrics">
            <div>
              <dt>Area</dt>
              <dd>{formatArea(numberProperty(properties, 'area_m2'))}</dd>
            </div>
            {selected.kind === 'parcel' && (
              <>
                <div>
                  <dt>Buildable</dt>
                  <dd>
                    {formatArea(numberProperty(properties, 'buildable_area_m2'))}
                  </dd>
                </div>
                <div>
                  <dt>Frontage</dt>
                  <dd>{formatLength(numberProperty(properties, 'frontage_m'))}</dd>
                </div>
                <div>
                  <dt>Buildable ratio</dt>
                  <dd>
                    {numberProperty(properties, 'buildable_ratio') === null
                      ? '—'
                      : `${(
                          (numberProperty(properties, 'buildable_ratio') ?? 0) * 100
                        ).toFixed(1)}%`}
                  </dd>
                </div>
              </>
            )}
            <div>
              <dt>Zone</dt>
              <dd>{textProperty(properties, 'zone_class') ?? '—'}</dd>
            </div>
            {selected.kind === 'block' && (
              <div>
                <dt>Association</dt>
                <dd>{textProperty(properties, 'association_status') ?? '—'}</dd>
              </div>
            )}
          </dl>

          <div className="block-parcel-flags">
            <strong>Validation flags</strong>
            {flags.length === 0 ? (
              <span className="flag flag-ok">No persisted flags</span>
            ) : (
              flags.map((flag) => (
                <span className="flag flag-warning" key={flag}>
                  {flag}
                </span>
              ))
            )}
          </div>

          <details className="block-parcel-details">
            <summary>Persisted properties</summary>
            <dl className="property-grid">
              {Object.entries(properties)
                .filter(([, value]) => value !== null && value !== undefined)
                .slice(0, 16)
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
          Кликните по generated block или planning parcel для metrics и validation flags.
        </p>
      )}

      {selectedRun && (
        <p className="helper-text">
          Run {selectedRun.id.slice(0, 8)} · {selectedRun.mode} ·{' '}
          {selectedRun.generated_block_count} blocks ·{' '}
          {selectedRun.generated_parcel_count} parcels persisted.
        </p>
      )}
    </section>
  )
}
