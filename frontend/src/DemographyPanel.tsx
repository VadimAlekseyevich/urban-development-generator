import {
  useCallback,
  useEffect,
  useMemo,
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
  viewportBounds,
  type GeoJsonFeatureCollection,
} from './sourceLayers'

const VIEWPORT_LIMIT = 1500
const SOURCE_ID = 'demography-blocks-ui-source'
const FILL_ID = 'demography-blocks-ui-fill'
const LINE_ID = 'demography-blocks-ui-line'

type LoadStatus = 'idle' | 'loading' | 'ready' | 'error'
type MetricMode = 'density' | 'population' | 'jobs'

type DemographyRunSummary = {
  id: string
  status: string
  population: number
}

type AgeGroupMetric = {
  code: string
  min_age: number
  max_age: number | null
  residents: number
  share: number
}

type DemographyMetrics = {
  scenario_version: string
  block_count: number
  population: number
  population_density_per_km2: number
  jobs_estimate: number
  age_groups: AgeGroupMetric[]
}

type DemographyResponse = GeoJsonFeatureCollection & {
  truncated: boolean
}

type SelectedBlock = {
  blockKey: string
  zoneClass: string
  population: number
  density: number
  jobs: number
  ageGroups: AgeGroupMetric[]
}

type DemographyPanelProps = {
  apiBase: string
  map: MapLibreMap | null
  projectId: string | null
}

function metricProperty(mode: MetricMode): string {
  if (mode === 'population') return 'population'
  if (mode === 'jobs') return 'jobs_estimate'
  return 'population_density_per_km2'
}

function metricLabel(mode: MetricMode): string {
  if (mode === 'population') return 'Population'
  if (mode === 'jobs') return 'Jobs'
  return 'Density'
}

function fillExpression(mode: MetricMode): ExpressionSpecification {
  const property = metricProperty(mode)
  const stops =
    mode === 'density'
      ? [0, 250, 1000, 3000]
      : mode === 'population'
        ? [0, 50, 250, 1000]
        : [0, 10, 100, 500]
  return [
    'interpolate',
    ['linear'],
    ['coalesce', ['to-number', ['get', property]], 0],
    stops[0],
    '#f8fafc',
    stops[1],
    '#bae6fd',
    stops[2],
    '#38bdf8',
    stops[3],
    '#0c4a6e',
  ] as ExpressionSpecification
}

function ensureLayers(map: MapLibreMap, mode: MetricMode): void {
  if (!map.getSource(SOURCE_ID)) {
    map.addSource(SOURCE_ID, {
      type: 'geojson',
      data: EMPTY_FEATURE_COLLECTION,
    })
  }
  if (!map.getLayer(FILL_ID)) {
    map.addLayer({
      id: FILL_ID,
      type: 'fill',
      source: SOURCE_ID,
      paint: {
        'fill-color': fillExpression(mode),
        'fill-opacity': 0.68,
      },
    })
  }
  if (!map.getLayer(LINE_ID)) {
    map.addLayer({
      id: LINE_ID,
      type: 'line',
      source: SOURCE_ID,
      paint: {
        'line-color': '#0f172a',
        'line-width': 1,
        'line-opacity': 0.65,
      },
    })
  }
}

function setData(map: MapLibreMap, data: GeoJsonFeatureCollection): void {
  const source = map.getSource(SOURCE_ID) as GeoJSONSource | undefined
  source?.setData(data)
}

function finiteProperty(
  properties: Record<string, unknown>,
  key: string,
): number {
  const value = properties[key]
  return typeof value === 'number' && Number.isFinite(value) ? value : 0
}

function selectedBlock(feature: MapGeoJSONFeature): SelectedBlock {
  const properties = feature.properties ?? {}
  const rawAgeGroups = properties.age_groups
  const ageGroups = Array.isArray(rawAgeGroups)
    ? rawAgeGroups.filter(
        (item): item is AgeGroupMetric =>
          typeof item === 'object' &&
          item !== null &&
          typeof (item as AgeGroupMetric).code === 'string',
      )
    : []
  return {
    blockKey:
      typeof properties.block_key === 'string'
        ? properties.block_key
        : String(feature.id ?? 'block'),
    zoneClass:
      typeof properties.zone_class === 'string' ? properties.zone_class : '—',
    population: finiteProperty(properties, 'population'),
    density: finiteProperty(properties, 'population_density_per_km2'),
    jobs: finiteProperty(properties, 'jobs_estimate'),
    ageGroups,
  }
}

function formatNumber(value: number, digits = 0): string {
  return new Intl.NumberFormat(undefined, {
    maximumFractionDigits: digits,
  }).format(value)
}

function formatAge(group: AgeGroupMetric): string {
  const range =
    group.max_age === null
      ? String(group.min_age) + '+'
      : String(group.min_age) + '–' + String(group.max_age)
  return group.code + ' (' + range + ')'
}

export function DemographyPanel({
  apiBase,
  map,
  projectId,
}: DemographyPanelProps) {
  const [runs, setRuns] = useState<DemographyRunSummary[]>([])
  const [runId, setRunId] = useState('')
  const [metrics, setMetrics] = useState<DemographyMetrics | null>(null)
  const [metricMode, setMetricMode] = useState<MetricMode>('density')
  const [visible, setVisible] = useState(true)
  const [status, setStatus] = useState<LoadStatus>('idle')
  const [message, setMessage] = useState(
    'Выберите проект с рассчитанной демографией.',
  )
  const [featureCount, setFeatureCount] = useState(0)
  const [truncated, setTruncated] = useState(false)
  const [selected, setSelected] = useState<SelectedBlock | null>(null)

  const activeRun = useMemo(
    () => runs.find((run) => run.id === runId) ?? null,
    [runId, runs],
  )

  useEffect(() => {
    if (!map) return
    ensureLayers(map, metricMode)
    return () => setData(map, EMPTY_FEATURE_COLLECTION)
  }, [map, metricMode])

  useEffect(() => {
    if (!map || !map.getLayer(FILL_ID)) return
    map.setPaintProperty(FILL_ID, 'fill-color', fillExpression(metricMode))
  }, [map, metricMode])

  useEffect(() => {
    if (!map) return
    for (const layerId of [FILL_ID, LINE_ID]) {
      if (map.getLayer(layerId)) {
        map.setLayoutProperty(
          layerId,
          'visibility',
          visible ? 'visible' : 'none',
        )
      }
    }
  }, [map, visible])

  useEffect(() => {
    if (!map) return
    const handleClick = (event: MapMouseEvent) => {
      if (!visible || !map.getLayer(FILL_ID)) return
      const hit = map.queryRenderedFeatures(event.point, {
        layers: [FILL_ID],
      })[0]
      setSelected(hit ? selectedBlock(hit) : null)
    }
    map.on('click', handleClick)
    return () => {
      map.off('click', handleClick)
    }
  }, [map, visible])

  useEffect(() => {
    setRuns([])
    setRunId('')
    setMetrics(null)
    setSelected(null)
    setFeatureCount(0)
    setTruncated(false)
    if (map) setData(map, EMPTY_FEATURE_COLLECTION)
    if (!projectId) {
      setStatus('idle')
      setMessage('Выберите проект с рассчитанной демографией.')
      return
    }

    const controller = new AbortController()
    setStatus('loading')
    setMessage('Загружаю демографические runs…')
    const url =
      apiBase +
      '/projects/' +
      encodeURIComponent(projectId) +
      '/demography-runs'
    void fetch(url, { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) {
          throw new Error(
            'HTTP ' + response.status + ': ' + (await response.text()),
          )
        }
        return (await response.json()) as DemographyRunSummary[]
      })
      .then((items) => {
        if (controller.signal.aborted) return
        setRuns(items)
        if (items.length === 0) {
          setStatus('ready')
          setMessage('Для проекта ещё нет сохранённых demography metrics.')
          return
        }
        setRunId(items[0].id)
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return
        setStatus('error')
        setMessage(error instanceof Error ? error.message : String(error))
      })
    return () => controller.abort()
  }, [apiBase, map, projectId])

  useEffect(() => {
    if (!projectId || !runId) return
    const controller = new AbortController()
    setStatus('loading')
    const url =
      apiBase +
      '/projects/' +
      encodeURIComponent(projectId) +
      '/demography-runs/' +
      encodeURIComponent(runId) +
      '/metrics'
    void fetch(url, { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) {
          throw new Error(
            'HTTP ' + response.status + ': ' + (await response.text()),
          )
        }
        return (await response.json()) as DemographyMetrics
      })
      .then((value) => {
        if (controller.signal.aborted) return
        setMetrics(value)
        setStatus('ready')
        setMessage('Demography metrics готовы. Карта обновляется по viewport.')
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return
        setStatus('error')
        setMessage(error instanceof Error ? error.message : String(error))
      })
    return () => controller.abort()
  }, [apiBase, projectId, runId])

  const loadViewport = useCallback(async () => {
    if (!map || !projectId || !runId || !visible) return
    const bounds = map.getBounds()
    const bbox = bboxParam(
      viewportBounds(
        bounds.getWest(),
        bounds.getSouth(),
        bounds.getEast(),
        bounds.getNorth(),
      ),
    )
    const url =
      apiBase +
      '/projects/' +
      encodeURIComponent(projectId) +
      '/demography-runs/' +
      encodeURIComponent(runId) +
      '/blocks/geojson?bbox=' +
      encodeURIComponent(bbox) +
      '&limit=' +
      String(VIEWPORT_LIMIT)
    const response = await fetch(url)
    if (!response.ok) {
      throw new Error(
        'Demography viewport: HTTP ' +
          response.status +
          ' ' +
          (await response.text()),
      )
    }
    const result = (await response.json()) as DemographyResponse
    setData(map, {
      type: 'FeatureCollection',
      features: result.features,
    })
    setFeatureCount(result.features.length)
    setTruncated(result.truncated)
  }, [apiBase, map, projectId, runId, visible])

  useEffect(() => {
    if (!map || !runId || !visible) return
    const refresh = () => {
      void loadViewport().catch((error: unknown) => {
        setStatus('error')
        setMessage(error instanceof Error ? error.message : String(error))
      })
    }
    map.on('moveend', refresh)
    refresh()
    return () => {
      map.off('moveend', refresh)
    }
  }, [loadViewport, map, runId, visible])

  function onRunChange(event: ChangeEvent<HTMLSelectElement>): void {
    setRunId(event.target.value)
    setSelected(null)
  }

  function onMetricChange(event: ChangeEvent<HTMLSelectElement>): void {
    setMetricMode(event.target.value as MetricMode)
  }

  return (
    <section className="panel demography-panel">
      <div className="section-heading section-heading-row">
        <div>
          <p className="section-kicker">S09 Demography</p>
          <h2>Population & demand</h2>
        </div>
        <span className="badge">{featureCount}</span>
      </div>

      <label className="demography-select">
        <span>Run</span>
        <select
          value={runId}
          onChange={onRunChange}
          disabled={runs.length === 0}
        >
          {runs.length === 0 && <option value="">Нет demography runs</option>}
          {runs.map((run) => (
            <option key={run.id} value={run.id}>
              {run.id.slice(0, 8)} · {run.population} people · {run.status}
            </option>
          ))}
        </select>
      </label>

      <div className="demography-controls">
        <label>
          <input
            type="checkbox"
            checked={visible}
            onChange={() => setVisible((current) => !current)}
          />
          <span>Choropleth</span>
        </label>
        <select value={metricMode} onChange={onMetricChange}>
          <option value="density">Density</option>
          <option value="population">Population</option>
          <option value="jobs">Jobs</option>
        </select>
      </div>

      {metrics && (
        <>
          <dl className="demography-metrics">
            <div>
              <dt>Population</dt>
              <dd>{formatNumber(metrics.population)}</dd>
            </div>
            <div>
              <dt>Density / km²</dt>
              <dd>{formatNumber(metrics.population_density_per_km2, 1)}</dd>
            </div>
            <div>
              <dt>Jobs</dt>
              <dd>{formatNumber(metrics.jobs_estimate, 1)}</dd>
            </div>
            <div>
              <dt>Blocks</dt>
              <dd>{formatNumber(metrics.block_count)}</dd>
            </div>
          </dl>

          <div className="demography-age-list">
            {metrics.age_groups.map((group) => (
              <div key={group.code}>
                <span>{formatAge(group)}</span>
                <strong>{formatNumber(group.residents)}</strong>
                <small>{(group.share * 100).toFixed(1)}%</small>
              </div>
            ))}
          </div>
        </>
      )}

      <div className={'load-state load-state-' + status}>{message}</div>
      {activeRun && (
        <p className="helper-text">
          {metricLabel(metricMode)} · scenario {metrics?.scenario_version ?? '—'}
          {truncated ? ' · viewport limit ' + String(VIEWPORT_LIMIT) + '+' : ''}
        </p>
      )}

      <div className="demography-inspector">
        <h3>{selected ? selected.blockKey : 'Block inspector'}</h3>
        {selected ? (
          <>
            <dl className="demography-metrics">
              <div>
                <dt>Zone</dt>
                <dd>{selected.zoneClass}</dd>
              </div>
              <div>
                <dt>Population</dt>
                <dd>{formatNumber(selected.population)}</dd>
              </div>
              <div>
                <dt>Density / km²</dt>
                <dd>{formatNumber(selected.density, 1)}</dd>
              </div>
              <div>
                <dt>Jobs</dt>
                <dd>{formatNumber(selected.jobs, 1)}</dd>
              </div>
            </dl>
            <div className="demography-age-list">
              {selected.ageGroups.map((group) => (
                <div key={group.code}>
                  <span>{formatAge(group)}</span>
                  <strong>{formatNumber(group.residents)}</strong>
                  <small>{(group.share * 100).toFixed(1)}%</small>
                </div>
              ))}
            </div>
          </>
        ) : (
          <p className="helper-text">
            Кликните по demographic block на карте, чтобы открыть population/jobs/cohorts.
          </p>
        )}
      </div>
    </section>
  )
}
