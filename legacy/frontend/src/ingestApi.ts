/** Real project-scoped ingest scheduling; ready state is owned by the worker. */

export type DatasetKind =
  | 'roads' | 'buildings' | 'landuse' | 'water'
  | 'facilities' | 'constraints' | 'dem'
export type IngestSourceFormat = 'geojson' | 'gpkg' | 'shapefile_zip' | 'geotiff'

export type IngestState = {
  project_id: string
  dataset_version_id: string
  dataset_id: string
  job_id: string
  status: string
  job_status: string
  attempt_count: number
  max_attempts: number
  retry_pending: boolean
  error_code: string | null
  error_message: string | null
}

export function inferIngestFormat(filename: string): IngestSourceFormat | null {
  const suffix = filename.toLowerCase().split('.').pop()
  if (suffix === 'geojson' || suffix === 'json') return 'geojson'
  if (suffix === 'gpkg') return 'gpkg'
  if (suffix === 'zip') return 'shapefile_zip'
  if (suffix === 'tif' || suffix === 'tiff') return 'geotiff'
  return null
}

async function decode(response: Response): Promise<IngestState> {
  if (!response.ok) {
    const detail = await response.text()
    throw new Error(`Импорт: HTTP ${response.status} ${detail.slice(0, 320)}`)
  }
  return (await response.json()) as IngestState
}

export async function scheduleIngest(
  apiBase: string,
  projectId: string,
  artifactId: string,
  kind: DatasetKind,
  sourceFormat: IngestSourceFormat,
): Promise<IngestState> {
  return decode(await fetch(
    `${apiBase}/projects/${encodeURIComponent(projectId)}/ingests`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        artifact_id: artifactId,
        dataset_kind: kind,
        source_format: sourceFormat,
      }),
    },
  ))
}

export async function readIngest(
  apiBase: string,
  projectId: string,
  versionId: string,
  signal: AbortSignal,
): Promise<IngestState> {
  return decode(await fetch(
    `${apiBase}/projects/${encodeURIComponent(projectId)}` +
    `/ingests/${encodeURIComponent(versionId)}`,
    { signal },
  ))
}
