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

export const MAX_BOUNDARY_FILE_BYTES = 2 * 1024 * 1024
export type BoundaryGeometry = { type: 'Polygon' | 'MultiPolygon'; coordinates: unknown[] }

function asObject(value: unknown): Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    throw new Error('Ожидается объект GeoJSON.')
  }
  return value as Record<string, unknown>
}

/** RFC 7946 longitude/latitude only. The API owns topology and coordinate validation. */
export function parseBoundaryGeoJSON(text: string): BoundaryGeometry {
  let obj = asObject(JSON.parse(text))
  if ('crs' in obj) throw new Error('Используйте GeoJSON в EPSG:4326 без поля crs.')
  if (obj.type === 'FeatureCollection') {
    if (!Array.isArray(obj.features) || obj.features.length !== 1) {
      throw new Error('Граница должна содержать ровно один объект Polygon или MultiPolygon.')
    }
    obj = asObject(obj.features[0])
    if (obj.type !== 'Feature' || 'crs' in obj) throw new Error('Некорректный GeoJSON Feature.')
  }
  if (obj.type === 'Feature') obj = asObject(obj.geometry)
  if ('crs' in obj || (obj.type !== 'Polygon' && obj.type !== 'MultiPolygon') ||
      !Array.isArray(obj.coordinates)) {
    throw new Error('Нужен Polygon или MultiPolygon в EPSG:4326.')
  }
  return { type: obj.type, coordinates: obj.coordinates }
}

export function saveProjectBoundary(
  apiBase: string, projectId: string, geometry: BoundaryGeometry, signal: AbortSignal,
): Promise<ProjectSummary> {
  return fetch(apiBase.replace(/\/$/, '') + '/projects/' + encodeURIComponent(projectId) + '/boundary', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ source_srid: 4326, geometry }),
    signal,
  }).then(checked<ProjectSummary>)
}
