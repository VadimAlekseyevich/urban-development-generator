export type MetricRunSummary = {
  id: string
  project_id: string
  status: string
  mode: string
  seed: number
  working_srid: number
  composite_score: number
  metric_count: number
  score_config_id: string
  score_config_version: string
  normalization_profile_id: string
  normalization_profile_version: string
  created_at: string
  finished_at: string | null
}

export type MetricRunListResponse = {
  project_id: string
  limit: number
  truncated: boolean
  runs: MetricRunSummary[]
}

export type MetricDirection =
  | 'HIGHER_IS_BETTER'
  | 'LOWER_IS_BETTER'
  | 'TARGET'
  | 'DESCRIPTIVE'

export type MetricDashboardMetric = {
  metric_id: string
  unit: string
  scope: string
  direction: MetricDirection
  metric_version: string
  raw_value: number | null
  normalized_value: number | null
  normalization_policy_version: string
  configured_weight: number
  normalized_weight: number
  contribution: number
  was_clamped: boolean
  was_missing: boolean
}

export type MetricDashboardResponse = {
  project_id: string
  run_id: string
  status: string
  mode: string
  seed: number
  working_srid: number
  composite_score: number
  score_config_id: string
  score_config_version: string
  normalization_profile_id: string
  normalization_profile_version: string
  metrics: MetricDashboardMetric[]
  created_at: string
  finished_at: string | null
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

export function fetchMetricRuns(
  apiBase: string,
  projectId: string,
  signal: AbortSignal,
): Promise<MetricRunListResponse> {
  const url =
    `${apiBase}/projects/${encodeURIComponent(projectId)}/metric-runs` +
    '?limit=100'
  return requestJson<MetricRunListResponse>(url, signal, 'metric runs')
}

export function fetchMetricDashboard(
  apiBase: string,
  projectId: string,
  runId: string,
  signal: AbortSignal,
): Promise<MetricDashboardResponse> {
  const url =
    `${apiBase}/projects/${encodeURIComponent(projectId)}` +
    `/metric-runs/${encodeURIComponent(runId)}/metrics`
  return requestJson<MetricDashboardResponse>(url, signal, 'metrics dashboard')
}
