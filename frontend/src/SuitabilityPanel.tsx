import { useEffect, useMemo, useRef, useState, type ChangeEvent, type FormEvent } from 'react'
import type { Map as MapLibreMap } from 'maplibre-gl'

import { isUuid } from './sourceLayers'
import {
  factorNormalizationLabel,
  formatPercent,
  formatScore,
  type SuitabilityLayerMetadata,
} from './suitabilityLayer'

const SUITABILITY_SOURCE_ID = 'suitability-preview'
const SUITABILITY_LAYER_ID = 'suitability-preview-raster'
const PREVIEW_DIMENSION = 2048

type LoadStatus = 'idle' | 'loading' | 'ready' | 'error'

type SuitabilityPanelProps = {
  apiBase: string
  map: MapLibreMap | null
}

function initialArtifactId(): string {
  return new URLSearchParams(window.location.search).get('suitability_artifact_id') ?? ''
}

function removeSuitabilityLayer(map: MapLibreMap): void {
  if (map.getLayer(SUITABILITY_LAYER_ID)) map.removeLayer(SUITABILITY_LAYER_ID)
  if (map.getSource(SUITABILITY_SOURCE_ID)) map.removeSource(SUITABILITY_SOURCE_ID)
}

export function SuitabilityPanel({ apiBase, map }: SuitabilityPanelProps) {
  const initialId = initialArtifactId()
  const [artifactInput, setArtifactInput] = useState(initialId)
  const [artifactId, setArtifactId] = useState(() => (isUuid(initialId) ? initialId : ''))
  const [formError, setFormError] = useState<string | null>(null)
  const [status, setStatus] = useState<LoadStatus>(artifactId ? 'loading' : 'idle')
  const [message, setMessage] = useState(
    artifactId ? 'Загружаю suitability artifact…' : 'Укажите Artifact ID результата suitability.',
  )
  const [metadata, setMetadata] = useState<SuitabilityLayerMetadata | null>(null)
  const [visible, setVisible] = useState(true)
  const [opacity, setOpacity] = useState(0.62)
  const abortRef = useRef<AbortController | null>(null)

  const previewUrl = useMemo(() => {
    if (!artifactId || !metadata) return null
    return (
      `${apiBase}/suitability-artifacts/${encodeURIComponent(artifactId)}/preview.png` +
      `?max_dimension=${PREVIEW_DIMENSION}&v=${encodeURIComponent(metadata.checksum)}`
    )
  }, [apiBase, artifactId, metadata])

  useEffect(() => {
    if (!map) return
    removeSuitabilityLayer(map)
    abortRef.current?.abort()
    setMetadata(null)

    if (!artifactId) {
      setStatus('idle')
      setMessage('Укажите Artifact ID результата suitability.')
      return
    }

    const controller = new AbortController()
    abortRef.current = controller
    setStatus('loading')
    setMessage('Загружаю suitability metadata…')

    void fetch(`${apiBase}/suitability-artifacts/${encodeURIComponent(artifactId)}`, {
      signal: controller.signal,
    })
      .then(async (response) => {
        if (!response.ok) {
          throw new Error(`suitability: HTTP ${response.status} ${await response.text()}`)
        }
        return (await response.json()) as SuitabilityLayerMetadata
      })
      .then((loaded) => {
        if (controller.signal.aborted) return
        setMetadata(loaded)
        setStatus('ready')
        setMessage(
          `Raster ${loaded.width}×${loaded.height}; valid cells: ${loaded.statistics.valid_cells}.`,
        )
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return
        setMetadata(null)
        setStatus('error')
        setMessage(error instanceof Error ? error.message : String(error))
      })

    return () => {
      controller.abort()
    }
  }, [apiBase, artifactId, map])

  useEffect(() => {
    if (!map || !metadata || !previewUrl) return
    removeSuitabilityLayer(map)
    map.addSource(SUITABILITY_SOURCE_ID, {
      type: 'image',
      url: previewUrl,
      coordinates: metadata.image_coordinates_wgs84,
    })
    map.addLayer(
      {
        id: SUITABILITY_LAYER_ID,
        type: 'raster',
        source: SUITABILITY_SOURCE_ID,
        layout: { visibility: visible ? 'visible' : 'none' },
        paint: {
          'raster-opacity': opacity,
          'raster-fade-duration': 0,
        },
      },
      map.getLayer('source-boundary-fill') ? 'source-boundary-fill' : undefined,
    )

    return () => {
      removeSuitabilityLayer(map)
    }
  }, [map, metadata, previewUrl])

  useEffect(() => {
    if (!map || !map.getLayer(SUITABILITY_LAYER_ID)) return
    map.setLayoutProperty(
      SUITABILITY_LAYER_ID,
      'visibility',
      visible ? 'visible' : 'none',
    )
  }, [map, visible])

  useEffect(() => {
    if (!map || !map.getLayer(SUITABILITY_LAYER_ID)) return
    map.setPaintProperty(SUITABILITY_LAYER_ID, 'raster-opacity', opacity)
  }, [map, opacity])

  function applyArtifact(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault()
    const value = artifactInput.trim()
    if (value && !isUuid(value)) {
      setFormError('Artifact ID должен быть UUID.')
      return
    }

    setFormError(null)
    setArtifactId(value)
    const url = new URL(window.location.href)
    if (value) url.searchParams.set('suitability_artifact_id', value)
    else url.searchParams.delete('suitability_artifact_id')
    window.history.replaceState({}, '', url)
  }

  function updateArtifactInput(event: ChangeEvent<HTMLInputElement>): void {
    setArtifactInput(event.target.value)
  }

  function updateOpacity(event: ChangeEvent<HTMLInputElement>): void {
    setOpacity(Number(event.target.value))
  }

  function fitSuitability(): void {
    if (!map || !metadata) return
    const [west, south, east, north] = metadata.wgs84_bounds
    map.fitBounds(
      [
        [west, south],
        [east, north],
      ],
      { padding: 56, maxZoom: 16, duration: 450 },
    )
  }

  const stats = metadata?.statistics ?? null

  return (
    <section className="panel suitability-panel">
      <div className="section-heading section-heading-row">
        <div>
          <p className="section-kicker">Suitability</p>
          <h2>Карта пригодности</h2>
        </div>
        {metadata && <span className="badge">0–1</span>}
      </div>

      <form className="context-form" onSubmit={applyArtifact}>
        <label>
          <span>Suitability Artifact ID</span>
          <input
            value={artifactInput}
            onChange={updateArtifactInput}
            placeholder="UUID artifact"
            autoComplete="off"
          />
        </label>
        {formError && <p className="form-error">{formError}</p>}
        <button className="button button-primary" type="submit" disabled={!map}>
          Открыть suitability
        </button>
      </form>

      <div className={`load-state load-state-${status}`}>{message}</div>

      {metadata && stats && (
        <>
          <div className="suitability-controls">
            <label className="suitability-toggle">
              <input
                type="checkbox"
                checked={visible}
                onChange={() => setVisible((current) => !current)}
              />
              <span>Показывать raster</span>
            </label>
            <label className="opacity-control">
              <span>Opacity {Math.round(opacity * 100)}%</span>
              <input
                type="range"
                min="0.1"
                max="1"
                step="0.05"
                value={opacity}
                onChange={updateOpacity}
              />
            </label>
          </div>

          <div className="suitability-legend" aria-label="Suitability legend">
            <div className="suitability-gradient" />
            <div className="suitability-legend-labels">
              <span>0 низкая</span>
              <span>0.5</span>
              <span>1 высокая</span>
            </div>
            <div className="hard-mask-key">
              <span className="hard-mask-swatch" /> hard exclusion
            </div>
          </div>

          <div className="button-row">
            <button className="button" type="button" onClick={fitSuitability}>
              Fit suitability
            </button>
            <button
              className="button"
              type="button"
              onClick={() => setArtifactId((current) => `${current}`)}
            >
              Обновить
            </button>
          </div>

          <dl className="property-grid suitability-stats">
            <div>
              <dt>Valid cells</dt>
              <dd>
                {stats.valid_cells} ({formatPercent(stats.valid_cells, stats.total_cells)})
              </dd>
            </div>
            <div>
              <dt>Hard excluded</dt>
              <dd>
                {stats.hard_excluded_cells} ({formatPercent(stats.hard_excluded_cells, stats.total_cells)})
              </dd>
            </div>
            <div>
              <dt>Invalid data</dt>
              <dd>{stats.invalid_data_cells}</dd>
            </div>
            <div>
              <dt>Mean / P50</dt>
              <dd>
                {formatScore(stats.mean_score)} / {formatScore(stats.p50_score)}
              </dd>
            </div>
            <div>
              <dt>Minimum ≥ {formatScore(stats.minimum_score_threshold)}</dt>
              <dd>
                {stats.meets_minimum_cells} ({formatPercent(stats.meets_minimum_cells, stats.valid_cells)})
              </dd>
            </div>
            {stats.preferred_score_threshold !== null && stats.preferred_cells !== null && (
              <div>
                <dt>Preferred ≥ {formatScore(stats.preferred_score_threshold)}</dt>
                <dd>
                  {stats.preferred_cells} ({formatPercent(stats.preferred_cells, stats.valid_cells)})
                </dd>
              </div>
            )}
            <div>
              <dt>Grid / CRS</dt>
              <dd>
                {metadata.width}×{metadata.height} / EPSG:{metadata.working_srid}
              </dd>
            </div>
            <div>
              <dt>Config</dt>
              <dd>{metadata.config_version}</dd>
            </div>
          </dl>

          <div className="factor-list">
            <p className="section-kicker">Factors</p>
            {metadata.factors.map((factor) => (
              <div className="factor-row" key={factor.code}>
                <div>
                  <strong>{factor.code}</strong>
                  <span>{factor.version}</span>
                </div>
                <div>
                  <strong>w={factor.weight.toFixed(3)}</strong>
                  <span>{factorNormalizationLabel(factor)}</span>
                </div>
              </div>
            ))}
          </div>

          <p className="helper-text">
            Hard mask: {metadata.hard_exclusion_source_codes.join(', ') || '—'}
          </p>
        </>
      )}
    </section>
  )
}
