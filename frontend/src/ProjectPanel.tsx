import { useEffect, useState, type FormEvent } from 'react'
import { createProject, listProjects, type ProjectSummary } from './projectApi'
import './workspace.css'

type Props = {
  apiBase: string
  selectedProjectId: string | null
  onProjectSelect: (projectId: string) => void
}

export function ProjectPanel({ apiBase, selectedProjectId, onProjectSelect }: Props) {
  const [projects, setProjects] = useState<ProjectSummary[]>([])
  const [offset, setOffset] = useState(0)
  const [refresh, setRefresh] = useState(0)
  const [loading, setLoading] = useState(true)
  const [listingError, setListingError] = useState<string | null>(null)
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [workingSrid, setWorkingSrid] = useState('')
  const [creating, setCreating] = useState(false)
  const [createError, setCreateError] = useState<string | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    setListingError(null)
    void listProjects(apiBase, controller.signal, offset)
      .then((result) => { if (!controller.signal.aborted) setProjects(result) })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return
        setProjects([])
        setListingError(error instanceof Error ? error.message : String(error))
      })
      .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [apiBase, offset, refresh])

  async function submit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    const srid = Number(workingSrid)
    if (!name.trim() || !/^\d+$/.test(workingSrid) || !Number.isSafeInteger(srid) || srid <= 0) {
      setCreateError('Укажите название и корректный метрический EPSG/SRID.')
      return
    }
    setCreating(true)
    setCreateError(null)
    try {
      const created = await createProject(apiBase, {
        name: name.trim(), description: description.trim() || null, working_srid: srid,
      })
      setName('')
      setDescription('')
      setWorkingSrid('')
      setOffset(0)
      setRefresh((value) => value + 1)
      onProjectSelect(created.id)
    } catch (error: unknown) {
      setCreateError(error instanceof Error ? error.message : String(error))
    } finally {
      setCreating(false)
    }
  }

  return (
    <section className="panel" aria-label="Проекты">
      <div className="section-heading">
        <p className="section-kicker">Workspace · Projects</p>
        <h2>Проекты</h2>
      </div>
      <p className="helper-text">
        Выберите существующий проект или создайте новый с метрическим working CRS.
      </p>
      <div className="upload-versions-heading">
        <strong>Существующие проекты</strong>
        <button type="button" className="button" disabled={loading}
          onClick={() => setRefresh((value) => value + 1)}>Обновить</button>
      </div>
      {loading && <p role="status" className="helper-text">Загружаю проекты…</p>}
      {listingError && <p role="alert" className="form-error">{listingError}</p>}
      {!loading && !listingError && projects.length === 0 &&
        <p className="helper-text">На этой странице пока нет проектов.</p>}
      <div className="workspace-project-list">
        {projects.map((project) => (
          <button key={project.id} className="workspace-project-item" type="button"
            onClick={() => onProjectSelect(project.id)}
            aria-pressed={project.id === selectedProjectId}>
            <strong>{project.name}</strong>
            <small>EPSG:{project.working_srid} · {project.id.slice(0, 8)}</small>
          </button>
        ))}
      </div>
      <div className="upload-version-nav">
        <button type="button" className="button" disabled={loading || offset === 0}
          onClick={() => setOffset((value) => Math.max(0, value - 50))}>Назад</button>
        <span>{offset + 1}–{offset + projects.length}</span>
        <button type="button" className="button" disabled={loading || projects.length < 50}
          onClick={() => setOffset((value) => value + 50)}>Далее</button>
      </div>

      <form className="context-form workspace-create-project" onSubmit={(event) => { void submit(event) }}>
        <h3>Новый проект</h3>
        <label><span>Название</span><input value={name} maxLength={200} required
          onChange={(event) => setName(event.target.value)} /></label>
        <label><span>Описание</span><input value={description}
          onChange={(event) => setDescription(event.target.value)} /></label>
        <label><span>Метрический EPSG / working SRID</span>
          <input value={workingSrid} type="number" min="1" step="1" required
            placeholder="Например, 32637" onChange={(event) => setWorkingSrid(event.target.value)} /></label>
        {createError && <p role="alert" className="form-error">{createError}</p>}
        <button className="button button-primary" disabled={creating} type="submit">
          {creating ? 'Создание…' : 'Создать проект'}
        </button>
      </form>
    </section>
  )
}
