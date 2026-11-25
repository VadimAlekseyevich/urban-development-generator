import { WORKSPACE_VIEWS, type WorkspaceView } from './workspaceContext'

const TITLES: Record<WorkspaceView, string> = {
  data: 'Данные', map: 'Карта', generation: 'Генерация', analysis: 'Анализ',
}

type Props = {
  active: WorkspaceView
  onChange: (next: WorkspaceView) => void
  projectId: string | null
  datasetVersionId: string | null
  runId: string | null
}

export function WorkspaceTabs({ active, onChange, projectId, datasetVersionId, runId }: Props) {
  return (
    <div className="workspace-navigation">
      <nav className="workspace-tabs" aria-label="Разделы рабочего пространства">
        {WORKSPACE_VIEWS.map((view) => (
          <button key={view} type="button" className="workspace-tab"
            aria-current={view === active ? 'page' : undefined}
            onClick={() => onChange(view)}>{TITLES[view]}</button>
        ))}
      </nav>
      <div className="workspace-context-summary" aria-live="polite">
        <span>Проект: {projectId ? projectId.slice(0, 8) : 'не выбран'}</span>
        <span>Версия: {datasetVersionId ? datasetVersionId.slice(0, 8) : 'не выбрана'}</span>
        <span>Run: {runId ? runId.slice(0, 8) : 'не закреплён'}</span>
      </div>
    </div>
  )
}
