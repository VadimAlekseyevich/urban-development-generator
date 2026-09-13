import {
  Component,
  useEffect,
  useMemo,
  useState,
  type ErrorInfo,
  type ReactNode,
} from 'react'

import {
  clearDiagnostics,
  readDiagnostics,
  recordDiagnostic,
  subscribeDiagnostics,
  type RuntimeDiagnostic,
} from './diagnostics'

type BoundaryProps = { children: ReactNode }
type BoundaryState = { crashed: boolean; message: string }

class FrontendErrorBoundary extends Component<BoundaryProps, BoundaryState> {
  state: BoundaryState = { crashed: false, message: '' }

  static getDerivedStateFromError(error: unknown): BoundaryState {
    const message = error instanceof Error ? error.message : String(error)
    return { crashed: true, message }
  }

  componentDidCatch(error: unknown, info: ErrorInfo): void {
    recordDiagnostic('react.error-boundary', 'React subtree crashed', {
      error: error instanceof Error ? error.stack ?? error.message : String(error),
      componentStack: info.componentStack,
    })
  }

  render(): ReactNode {
    if (!this.state.crashed) return this.props.children

    return (
      <main className="runtime-crash-shell">
        <section className="runtime-crash-card">
          <p className="runtime-kicker">Frontend runtime error</p>
          <h1>Интерфейс упал, но диагностика сохранена</h1>
          <p>{this.state.message || 'Неизвестная ошибка React.'}</p>
          <p className="runtime-muted">
            Откройте блок Diagnostics справа снизу и пришлите его содержимое. Логи переживают
            перезагрузку страницы.
          </p>
          <button type="button" onClick={() => window.location.reload()}>
            Перезагрузить страницу
          </button>
        </section>
      </main>
    )
  }
}

function formatEntry(entry: RuntimeDiagnostic): string {
  const detail = entry.detail ? `\n${entry.detail}` : ''
  return `${entry.at} [${entry.level}] ${entry.source}: ${entry.message}${detail}`
}

function DiagnosticOverlay() {
  const [entries, setEntries] = useState<RuntimeDiagnostic[]>(() => readDiagnostics())
  const [open, setOpen] = useState(() => entries.some((entry) => entry.level === 'error'))

  useEffect(() => {
    return subscribeDiagnostics(() => {
      const next = readDiagnostics()
      setEntries(next)
      if (next.some((entry) => entry.level === 'error')) setOpen(true)
    })
  }, [])

  const text = useMemo(() => entries.map(formatEntry).join('\n\n'), [entries])
  const errorCount = entries.filter((entry) => entry.level === 'error').length

  function clear(): void {
    clearDiagnostics()
    setEntries([])
    setOpen(false)
  }

  return (
    <aside className={`runtime-diagnostics ${open ? 'runtime-diagnostics-open' : ''}`}>
      <button
        className="runtime-diagnostics-toggle"
        type="button"
        onClick={() => setOpen((current) => !current)}
      >
        Diagnostics · {errorCount} errors · diag-v1
      </button>
      {open && (
        <div className="runtime-diagnostics-panel">
          <div className="runtime-diagnostics-heading">
            <strong>Runtime diagnostics</strong>
            <div>
              <button type="button" onClick={() => window.location.reload()}>
                Reload
              </button>
              <button type="button" onClick={clear}>
                Clear
              </button>
            </div>
          </div>
          <textarea
            aria-label="Runtime diagnostics log"
            readOnly
            value={text || 'Ошибок пока нет.'}
            onFocus={(event) => event.currentTarget.select()}
          />
          <p>Кликните в лог — текст выделится целиком для копирования.</p>
        </div>
      )}
    </aside>
  )
}

export function RuntimeDiagnostics({ children }: BoundaryProps) {
  return (
    <>
      <FrontendErrorBoundary>{children}</FrontendErrorBoundary>
      <DiagnosticOverlay />
    </>
  )
}
