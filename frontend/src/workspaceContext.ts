/** Owner-qualified workspace state shared by project, map, run and compare UI. */
export type WorkspaceSelection = {
  projectId: string | null
  datasetVersionId: string | null
  runId: string | null
}

export const WORKSPACE_VIEWS = ['data', 'map', 'generation', 'analysis'] as const
export type WorkspaceView = (typeof WORKSPACE_VIEWS)[number]

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

export function validWorkspaceId(value: string | null | undefined): string | null {
  const normalized = value?.trim() ?? ''
  return UUID.test(normalized) ? normalized : null
}

export function readWorkspaceSelection(search: string): WorkspaceSelection {
  const params = new URLSearchParams(search)
  const projectId = validWorkspaceId(params.get('project_id'))
  return {
    projectId,
    datasetVersionId: projectId ? validWorkspaceId(params.get('dataset_version_id')) : null,
    runId: projectId ? validWorkspaceId(params.get('map_run_id')) : null,
  }
}

export function readWorkspaceView(search: string): WorkspaceView {
  const value = new URLSearchParams(search).get('workspace_view')
  return WORKSPACE_VIEWS.find((view) => view === value) ?? 'data'
}

export function selectProject(current: WorkspaceSelection, projectId: string | null): WorkspaceSelection {
  const next = validWorkspaceId(projectId)
  return next === current.projectId ? current
    : { projectId: next, datasetVersionId: null, runId: null }
}

export function selectDatasetVersion(
  current: WorkspaceSelection, versionId: string | null,
): WorkspaceSelection {
  return { ...current, datasetVersionId: current.projectId ? validWorkspaceId(versionId) : null, runId: null }
}

export function selectMapRun(current: WorkspaceSelection, runId: string | null): WorkspaceSelection {
  return { ...current, runId: current.projectId ? validWorkspaceId(runId) : null }
}

export function workspaceSearch(
  search: string, selection: WorkspaceSelection, view: WorkspaceView,
): string {
  const params = new URLSearchParams(search)
  const fields = {
    project_id: selection.projectId,
    dataset_version_id: selection.projectId ? selection.datasetVersionId : null,
    map_run_id: selection.projectId ? selection.runId : null,
  }
  for (const [key, value] of Object.entries(fields)) {
    if (value) params.set(key, value)
    else params.delete(key)
  }
  params.set('workspace_view', view)
  return '?' + params.toString()
}
