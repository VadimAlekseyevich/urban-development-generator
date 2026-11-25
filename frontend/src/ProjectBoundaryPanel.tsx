import { useEffect, useRef, useState, type FormEvent } from 'react'
import { MAX_BOUNDARY_FILE_BYTES, parseBoundaryGeoJSON, saveProjectBoundary } from './projectApi'

type Props = {
  apiBase: string
  projectId: string | null
  boundaryAvailable: boolean
  onSaved: () => void
}

export function ProjectBoundaryPanel({ apiBase, projectId, boundaryAvailable, onSaved }: Props) {
  const [file, setFile] = useState<File | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)
  const request = useRef<AbortController | null>(null)

  useEffect(() => () => request.current?.abort(), [])

  async function submit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    if (!projectId || !file || request.current) return
    setSaved(false)
    setError(null)
    if (file.size > MAX_BOUNDARY_FILE_BYTES) {
      setError('Файл границы слишком большой (максимум 2 МиБ).')
      return
    }
    const controller = new AbortController()
    request.current = controller
    setBusy(true)
    try {
      const geometry = parseBoundaryGeoJSON(await file.text())
      if (controller.signal.aborted) return
      await saveProjectBoundary(apiBase, projectId, geometry, controller.signal)
      if (controller.signal.aborted) return
      setSaved(true)
      onSaved()
    } catch (caught: unknown) {
      if (!controller.signal.aborted) {
        setError(caught instanceof Error ? caught.message : String(caught))
      }
    } finally {
      if (request.current === controller) request.current = null
      if (!controller.signal.aborted) setBusy(false)
    }
  }

  return (
    <section className="panel" aria-label="Граница территории">
      <div className="section-heading">
        <p className="section-kicker">Workspace · Boundary</p>
        <h2>Граница территории</h2>
      </div>
      <p className="helper-text">
        {boundaryAvailable ? 'Граница задана.' : 'Задайте территорию перед генерацией.'}
        {' '}Загрузите GeoJSON Polygon или MultiPolygon в EPSG:4326.
        После первого запуска изменение границы запрещено.
      </p>
      <form className="context-form" onSubmit={(event) => { void submit(event) }}>
        <label><span>Файл границы GeoJSON</span>
          <input type="file" accept=".geojson,.json,application/geo+json,application/json"
            disabled={!projectId || busy}
            onChange={(event) => {
              setFile(event.target.files?.[0] ?? null)
              setSaved(false)
              setError(null)
            }} />
        </label>
        {error && <p className="form-error" role="alert">{error}</p>}
        {saved && <p className="helper-text" role="status">Граница сохранена.</p>}
        <button className="button button-primary" type="submit" disabled={!projectId || !file || busy}>
          {busy ? 'Сохранение…' : 'Сохранить границу'}
        </button>
      </form>
    </section>
  )
}
