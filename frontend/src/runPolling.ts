/** Single-flight, bounded polling of authoritative run/job/stage snapshots. */

import { useEffect, useState } from 'react'

import { fetchRunList, type RunState } from './runApi'

export const RUN_POLL_INTERVAL_MS = 3000
export const RUN_POLL_MAX_FAILURES = 3
export const RUN_POLL_MAX_DELAY_MS = 12000

export function isActiveRun(run: RunState): boolean {
  return run.status === 'queued' || run.status === 'running'
}

export function nextRunPollDelay(
  hasActiveRun: boolean,
  consecutiveFailures: number,
): number | null {
  if (consecutiveFailures >= RUN_POLL_MAX_FAILURES) return null
  if (!hasActiveRun && consecutiveFailures === 0) return null
  return Math.min(
    RUN_POLL_MAX_DELAY_MS,
    RUN_POLL_INTERVAL_MS * 2 ** consecutiveFailures,
  )
}

export function useRunPolling(
  apiBase: string,
  projectId: string | null,
  refreshKey: number,
) {
  const [runs, setRuns] = useState<RunState[]>([])
  const [truncated, setTruncated] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setRuns([])
    setTruncated(false)
    setError(null)
    if (!projectId) {
      setLoading(false)
      return
    }

    let mounted = true
    let failures = 0
    let controller: AbortController | null = null
    let timer: ReturnType<typeof setTimeout> | null = null
    setLoading(true)

    async function tick(): Promise<void> {
      if (!projectId || !mounted) return
      controller = new AbortController()
      try {
        const snapshot = await fetchRunList(apiBase, projectId, controller.signal)
        if (!mounted) return
        failures = 0
        setRuns(snapshot.runs)
        setTruncated(snapshot.truncated)
        setLoading(false)
        setError(null)
        const delay = nextRunPollDelay(snapshot.runs.some(isActiveRun), failures)
        if (delay !== null) timer = setTimeout(() => { void tick() }, delay)
      } catch (caught: unknown) {
        if (!mounted) return
        failures += 1
        setLoading(false)
        const message = caught instanceof Error ? caught.message : String(caught)
        setError(
          failures >= RUN_POLL_MAX_FAILURES
            ? `${message}. Автообновление приостановлено; нажмите «Обновить».`
            : message,
        )
        const delay = nextRunPollDelay(true, failures)
        if (delay !== null) timer = setTimeout(() => { void tick() }, delay)
      }
    }

    void tick()
    return () => {
      mounted = false
      controller?.abort()
      if (timer !== null) clearTimeout(timer)
    }
  }, [apiBase, projectId, refreshKey])

  return { runs, truncated, loading, error }
}
