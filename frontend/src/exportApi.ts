/** Bounded raw-metrics CSV export over persisted evaluations; no GIS recomputation. */

export async function fetchMetricsCsv(
  apiBase: string,
  projectId: string,
  runIds: readonly string[],
): Promise<Blob> {
  if (runIds.length < 1 || runIds.length > 10 || new Set(runIds).size !== runIds.length) {
    throw new Error('Экспорт требует 1–10 различных запусков.')
  }
  const response = await fetch(
    `${apiBase}/projects/${encodeURIComponent(projectId)}/exports/metrics.csv`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ run_ids: runIds }),
    },
  )
  if (!response.ok) {
    const detail = await response.text()
    throw new Error(`Экспорт: HTTP ${response.status} ${detail.slice(0, 300)}`)
  }
  return response.blob()
}

export function saveMetricsCsv(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}
