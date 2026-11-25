import { type Bounds, type GeoJsonFeatureCollection, bboxParam } from './sourceLayers'

export type ValidationRunSummary = {
  id: string
  project_id: string
  status: string
  mode: string
  seed: number
  working_srid: number
  violation_count: number
  hard_violation_count: number
  soft_violation_count: number
  spatial_violation_count: number
  created_at: string
  finished_at: string | null
}

export type ValidationRunListResponse = {
  project_id: string
  limit: number
  truncated: boolean
  runs: ValidationRunSummary[]
}

export type ViolationSoftPenalty = {
  raw_penalty: number
  weight: number
  weighted_penalty: number
  schema_version: number
}

export type ViolationDetail = {
  violation_index: number
  code: string
  severity: 'HARD' | 'SOFT'
  scope: string
  message: string
  entity_id: string | null
  has_problem_geometry: boolean
  soft_penalty: ViolationSoftPenalty | null
}

export type ViolationListResponse = {
  project_id: string
  run_id: string
  working_srid: number
  offset: number
  limit: number
  total: number
  truncated: boolean
  violations: ViolationDetail[]
}

export type ViolationGeoJSONResponse = GeoJsonFeatureCollection & {
  project_id: string
  run_id: string
  query_bbox: [number, number, number, number]
  geojson_crs: 'EPSG:4326'
  working_srid: number
  limit: number
  matching_count: number
  truncated: boolean
}

async function requestJson<T>(
  url: string,
  signal: AbortSignal,
  label: string,
): Promise<T> {
  const response = await fetch(url, { signal })
  if (!response.ok) {
    throw new Error(`${label}: HTTP ${response.status} ${await response.text()}`)
  }
  return (await response.json()) as T
}

export function fetchValidationRuns(
  apiBase: string,
  projectId: string,
  signal: AbortSignal,
): Promise<ValidationRunListResponse> {
  const url =
    `${apiBase}/projects/${encodeURIComponent(projectId)}/validation-runs` +
    '?limit=100'
  return requestJson<ValidationRunListResponse>(url, signal, 'validation runs')
}

export function fetchViolations(
  apiBase: string,
  projectId: string,
  runId: string,
  signal: AbortSignal,
): Promise<ViolationListResponse> {
  const url =
    `${apiBase}/projects/${encodeURIComponent(projectId)}` +
    `/validation-runs/${encodeURIComponent(runId)}/violations` +
    '?offset=0&limit=1000'
  return requestJson<ViolationListResponse>(url, signal, 'violations')
}

export function fetchViolationGeoJSON(
  apiBase: string,
  projectId: string,
  runId: string,
  bounds: Bounds,
  signal: AbortSignal,
): Promise<ViolationGeoJSONResponse> {
  const url =
    `${apiBase}/projects/${encodeURIComponent(projectId)}` +
    `/validation-runs/${encodeURIComponent(runId)}/violations/geojson` +
    `?bbox=${encodeURIComponent(bboxParam(bounds))}&limit=1000`
  return requestJson<ViolationGeoJSONResponse>(
    url,
    signal,
    'violation geometry',
  )
}
