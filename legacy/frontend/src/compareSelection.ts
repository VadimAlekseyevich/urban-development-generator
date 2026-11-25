/** Bounded, deterministic comparison selection and shared run-map pinning. */

export const MIN_COMPARE_RUNS = 2
export const MAX_COMPARE_RUNS = 10

/** The first selected item is the comparison baseline. No implicit sorting. */
export function toggleComparedRun(
  current: readonly string[],
  runId: string,
  checked: boolean,
): string[] {
  if (checked) {
    if (current.includes(runId)) return [...current]
    if (current.length >= MAX_COMPARE_RUNS) return [...current]
    return [...current, runId]
  }
  return current.filter((id) => id !== runId)
}

export function moveBaselineFirst(
  current: readonly string[],
  runId: string,
): string[] {
  if (!current.includes(runId)) return [...current]
  return [runId, ...current.filter((id) => id !== runId)]
}

/**
 * A compare map pin is authoritative for EVERY generated layer. If one
 * layer has no persisted read model for that run, show nothing rather
 * than silently switching that layer to a different generation run.
 * null means independent legacy panel selection.
 */
export function resolveMapRunId<T extends { id: string }>(
  available: readonly T[],
  localRunId: string,
  pinnedRunId: string | null,
): string {
  if (pinnedRunId === null) return localRunId
  return available.some((run) => run.id === pinnedRunId) ? pinnedRunId : ''
}
