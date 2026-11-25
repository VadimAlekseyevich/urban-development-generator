/** S12 run control wire contract. All statuses come from persisted API rows. */

export type RunLifecycleStatus =
  | 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled'

export type RunJob = {
  id: string
  status: RunLifecycleStatus
  attempt_count: number
  max_attempts: number
  cancel_requested_at: string | null
  error_class: string | null
  error_code: string | null
}

export type RunStage = {
  stage_name: string
  stage_version: string
  status: string
  progress_percent: number
  started_at: string | null
  finished_at: string | null
}

export type RunState = {
  id: string
  project_id: string
  rerun_source_id: string | null
  status: RunLifecycleStatus
  mode: 'EXPANSION' | 'FROM_SCRATCH'
  seed: number
  job: RunJob | null
  stages: RunStage[]
  error_message: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
}

export type RunList = {
  project_id: string
  limit: number
  truncated: boolean
  runs: RunState[]
}

export type RunCreate = {
  mode: RunState['mode']
  seed: number
  dataset_version_ids: string[]
  config_json: Record<string, unknown>
  config_schema_version: string
  commit_sha: string
}

async function jsonRequest<T>(
  url: string,
  init: RequestInit,
): Promise<T> {
  const response = await fetch(url, init)
  if (!response.ok) {
    const text = await response.text()
    throw new Error(`HTTP ${response.status}: ${text.slice(0, 400)}`)
  }
  return (await response.json()) as T
}

function runsUrl(apiBase: string, projectId: string): string {
  return `${apiBase}/projects/${encodeURIComponent(projectId)}/runs`
}

export function fetchRunList(
  apiBase: string,
  projectId: string,
  signal: AbortSignal,
): Promise<RunList> {
  return jsonRequest<RunList>(`${runsUrl(apiBase, projectId)}?limit=50`, { signal })
}

export function createRun(
  apiBase: string,
  projectId: string,
  payload: RunCreate,
): Promise<RunState> {
  return jsonRequest<RunState>(runsUrl(apiBase, projectId), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export function cancelRun(
  apiBase: string,
  projectId: string,
  runId: string,
): Promise<RunState> {
  return jsonRequest<RunState>(
    `${runsUrl(apiBase, projectId)}/${encodeURIComponent(runId)}/cancel`,
    { method: 'POST' },
  )
}

export function retryRun(
  apiBase: string,
  projectId: string,
  runId: string,
): Promise<RunState> {
  return jsonRequest<RunState>(
    `${runsUrl(apiBase, projectId)}/${encodeURIComponent(runId)}/retry`,
    { method: 'POST' },
  )
}
