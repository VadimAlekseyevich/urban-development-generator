/** Project lookup through the existing project REST API. */
export type ProjectSummary = {
  id: string
  name: string
  description: string | null
  working_srid: number
  created_at: string
  updated_at: string
}
export type ProjectCreate = { name: string; description: string | null; working_srid: number }

async function checked<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const body = await response.text()
    throw new Error('HTTP ' + response.status + ': ' + body.slice(0, 300))
  }
  return (await response.json()) as T
}

export function listProjects(apiBase: string, signal: AbortSignal, offset = 0): Promise<ProjectSummary[]> {
  if (!Number.isSafeInteger(offset) || offset < 0) throw new Error('Invalid project offset')
  return fetch(apiBase.replace(/\/$/, '') + '/projects?limit=50&offset=' + offset, { signal }).then(checked<ProjectSummary[]>)
}
export function createProject(apiBase: string, payload: ProjectCreate): Promise<ProjectSummary> {
  return fetch(apiBase.replace(/\/$/, '') + '/projects', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  }).then(checked<ProjectSummary>)
}
