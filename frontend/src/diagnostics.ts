export type DiagnosticLevel = 'info' | 'error'

export type RuntimeDiagnostic = {
  id: string
  at: string
  level: DiagnosticLevel
  source: string
  message: string
  detail: string | null
}

const STORAGE_KEY = 'udg:runtime-diagnostics:v1'
const EVENT_NAME = 'udg:runtime-diagnostic'
const MAX_ENTRIES = 40

function describe(value: unknown): string {
  if (value instanceof Error) {
    return value.stack?.trim() || `${value.name}: ${value.message}`
  }
  if (typeof value === 'string') return value
  try {
    return JSON.stringify(value)
  } catch {
    return String(value)
  }
}

function loadStored(): RuntimeDiagnostic[] {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw) as unknown
    if (!Array.isArray(parsed)) return []
    return parsed.filter((item): item is RuntimeDiagnostic => {
      if (!item || typeof item !== 'object') return false
      const candidate = item as Partial<RuntimeDiagnostic>
      return (
        typeof candidate.id === 'string' &&
        typeof candidate.at === 'string' &&
        (candidate.level === 'info' || candidate.level === 'error') &&
        typeof candidate.source === 'string' &&
        typeof candidate.message === 'string' &&
        (candidate.detail === null || typeof candidate.detail === 'string')
      )
    })
  } catch {
    return []
  }
}

function store(entries: RuntimeDiagnostic[]): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(entries.slice(-MAX_ENTRIES)))
  } catch {
    // Diagnostics must never become a new application failure.
  }
}

export function readDiagnostics(): RuntimeDiagnostic[] {
  return loadStored()
}

export function clearDiagnostics(): void {
  try {
    window.localStorage.removeItem(STORAGE_KEY)
  } catch {
    // Best effort only.
  }
  window.dispatchEvent(new CustomEvent(EVENT_NAME))
}

export function recordDiagnostic(
  source: string,
  message: string,
  detail: unknown = null,
  level: DiagnosticLevel = 'error',
): RuntimeDiagnostic {
  const entry: RuntimeDiagnostic = {
    id: `${Date.now()}-${Math.random().toString(16).slice(2)}`,
    at: new Date().toISOString(),
    level,
    source,
    message,
    detail: detail === null || detail === undefined ? null : describe(detail),
  }
  const entries = [...loadStored(), entry].slice(-MAX_ENTRIES)
  store(entries)

  if (level === 'error') {
    console.error(`[${source}] ${message}`, detail)
  } else {
    console.info(`[${source}] ${message}`, detail ?? '')
  }

  window.dispatchEvent(new CustomEvent(EVENT_NAME, { detail: entry }))
  return entry
}

export function subscribeDiagnostics(listener: () => void): () => void {
  const handler = () => listener()
  window.addEventListener(EVENT_NAME, handler)
  return () => window.removeEventListener(EVENT_NAME, handler)
}

let installed = false

export function installGlobalDiagnostics(): void {
  if (installed) return
  installed = true

  recordDiagnostic(
    'frontend.bootstrap',
    'Frontend runtime diagnostics installed',
    {
      href: `${window.location.origin}${window.location.pathname}`,
      userAgent: navigator.userAgent,
    },
    'info',
  )

  window.addEventListener('error', (event) => {
    recordDiagnostic(
      'window.error',
      event.message || 'Unhandled window error',
      event.error ?? `${event.filename}:${event.lineno}:${event.colno}`,
    )
  })

  window.addEventListener('unhandledrejection', (event) => {
    recordDiagnostic('window.unhandledrejection', 'Unhandled promise rejection', event.reason)
  })
}
