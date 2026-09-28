/** Persisted S12 comparison HTTP contract: never compute scores or GIS in the UI. */

export type CompareDirection =
  | 'HIGHER_IS_BETTER'
  | 'LOWER_IS_BETTER'
  | 'TARGET'
  | 'DESCRIPTIVE'

export type CompareValidationSummary = {
  violation_count: number
  hard_violation_count: number
  soft_violation_count: number
  spatial_violation_count: number
}

export type ComparedRun = {
  run_id: string
  seed: number
  mode: string
  working_srid: number
  score_config_id: string
  score_config_version: string
  normalization_profile_id: string
  normalization_profile_version: string
  composite_score: number
  score_delta_from_baseline: number | null
  score_rank: number | null
  validation: CompareValidationSummary
}

export type ComparedMetricValue = {
  run_id: string
  raw_value: number | null
  delta_from_baseline: number | null
  rank: number | null
}

export type ComparedMetric = {
  metric_id: string
  unit: string
  scope: string
  direction: CompareDirection
  definition_version: string
  values: ComparedMetricValue[]
}

export type RunComparison = {
  project_id: string
  baseline_run_id: string
  run_ids: string[]
  scores_comparable: boolean
  runs: ComparedRun[]
  metrics: ComparedMetric[]
}

export async function fetchRunComparison(
  apiBase: string,
  projectId: string,
  runIds: readonly string[],
  signal: AbortSignal,
): Promise<RunComparison> {
  const response = await fetch(
    `${apiBase}/projects/${encodeURIComponent(projectId)}/compare`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ run_ids: runIds }),
      signal,
    },
  )
  if (!response.ok) {
    throw new Error(`Compare: HTTP ${response.status}: ${(await response.text()).slice(0, 400)}`)
  }
  return (await response.json()) as RunComparison
}
