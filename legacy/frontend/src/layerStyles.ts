/** Renderer-only MapLibre recipes. No fetch URLs, owner selection or API calls here.
 * Catalog IDs/transport live in layerRegistry; panels own UI controls/read models.
 */
import type { LayerId } from './layerRegistry'
import type { SourceLayerKey } from './sourceLayers'

export type LayerStylePart = Readonly<{
  id: string
  type: 'fill' | 'line' | 'circle' | 'raster'
  paint: Readonly<Record<string, unknown>>
  filter?: readonly unknown[]
}>
const polygons = ['==', ['geometry-type'], 'Polygon'] as const
const lines = ['==', ['geometry-type'], 'LineString'] as const
const points = ['==', ['geometry-type'], 'Point'] as const
const severityColors = [
  'match', ['get', 'severity'], 'HARD', '#dc2626', 'SOFT', '#f59e0b', '#64748b',
] as const
const roadColors = [
  'match', ['get', 'road_class'], 'arterial', '#dc2626',
  'collector', '#f59e0b', 'local', '#0ea5e9', '#7c3aed',
] as const
const facilityColors = [
  'match', ['get', 'facility_class'], 'education', '#2563eb', 'healthcare', '#dc2626',
  'retail', '#ca8a04', 'recreation', '#16a34a', '#7c3aed',
] as const
const existing = ['==', ['get', 'origin'], 'existing'] as const
const generated = ['==', ['get', 'origin'], 'generated'] as const
const and = (origin: readonly unknown[], kind: readonly unknown[]): readonly unknown[] => [
  'all', origin, kind,
]
/** IDs retain existing map layer/source bindings until S13-T06 migrates the layer tree. */
export const MAP_LAYER_STYLES: Readonly<Record<LayerId, readonly LayerStylePart[]>> = {
  'analysis.suitability': [
    { id: 'suitability-preview-raster', type: 'raster', paint: { 'raster-opacity': 0.62, 'raster-fade-duration': 0 } },
  ],
  'project.boundary': [
    { id: 'source-boundary-fill', type: 'fill', paint: { 'fill-color': '#f59e0b', 'fill-opacity': 0.06 } },
    { id: 'source-boundary-line', type: 'line', paint: { 'line-color': '#f59e0b', 'line-width': 3, 'line-dasharray': [2, 2] } },
  ],
  'source.landuse': [
    { id: 'source-landuse-fill', type: 'fill', paint: { 'fill-color': '#65a30d', 'fill-opacity': 0.22 } },
    { id: 'source-landuse-line', type: 'line', paint: { 'line-color': '#4d7c0f', 'line-width': 1 } },
  ],
  'source.water': [
    { id: 'source-water-fill', type: 'fill', paint: { 'fill-color': '#0ea5e9', 'fill-opacity': 0.5 }, filter: polygons },
    { id: 'source-water-line', type: 'line', paint: { 'line-color': '#0284c7', 'line-width': 2 } },
  ],
  'source.constraints': [
    { id: 'source-constraints-fill', type: 'fill', paint: { 'fill-color': '#ef4444', 'fill-opacity': 0.2 }, filter: polygons },
    { id: 'source-constraints-line', type: 'line', paint: { 'line-color': '#dc2626', 'line-width': 2 } },
    { id: 'source-constraints-point', type: 'circle', paint: { 'circle-color': '#dc2626', 'circle-radius': 5 }, filter: points },
  ],
  'source.facilities': [
    { id: 'source-facilities-fill', type: 'fill', paint: { 'fill-color': '#64748b', 'fill-opacity': 0.25 }, filter: polygons },
    { id: 'source-facilities-line', type: 'line', paint: { 'line-color': '#475569', 'line-width': 2 } },
    { id: 'source-facilities-point', type: 'circle', paint: { 'circle-color': '#475569', 'circle-radius': 5 }, filter: points },
  ],
  'source.buildings': [
    { id: 'source-buildings-fill', type: 'fill', paint: { 'fill-color': '#d97706', 'fill-opacity': 0.52 } },
    { id: 'source-buildings-line', type: 'line', paint: { 'line-color': '#92400e', 'line-width': 0.8 } },
  ],
  'source.roads': [
    { id: 'source-roads-line', type: 'line', paint: { 'line-color': '#334155', 'line-width': ['interpolate', ['linear'], ['zoom'], 7, 1, 12, 2, 16, 4] } },
  ],
  'generated.zones': [
    { id: 'zoning-generated-fill', type: 'fill', paint: {
      'fill-color': ['match', ['get', 'zone_class'], 'residential', '#ea580c', 'mixed', '#7c3aed', 'public', '#2563eb', 'recreation', '#16a34a', '#64748b'],
      'fill-opacity': 0.48,
    } },
    { id: 'zoning-generated-line', type: 'line', paint: { 'line-color': '#0f172a', 'line-width': 1.4 } },
  ],
  'generated.demography': [
    { id: 'demography-blocks-ui-fill', type: 'fill', paint: { 'fill-color': '#38bdf8', 'fill-opacity': 0.68 } },
    { id: 'demography-blocks-ui-line', type: 'line', paint: { 'line-color': '#0f172a', 'line-width': 1, 'line-opacity': 0.65 } },
  ],
  'generated.blocks': [
    { id: 'blocks-ui-fill', type: 'fill', paint: { 'fill-color': ['match', ['get', 'association_status'], 'ASSOCIATED', '#2563eb', 'PARTIAL_OVERLAP', '#f59e0b', 'AMBIGUOUS_FULL_COVERAGE', '#7c3aed', 'NO_OVERLAP', '#dc2626', '#64748b'], 'fill-opacity': 0.18 } },
    { id: 'blocks-ui-line', type: 'line', paint: { 'line-color': ['match', ['get', 'association_status'], 'ASSOCIATED', '#1d4ed8', 'PARTIAL_OVERLAP', '#d97706', 'AMBIGUOUS_FULL_COVERAGE', '#6d28d9', 'NO_OVERLAP', '#b91c1c', '#475569'], 'line-width': ['interpolate', ['linear'], ['zoom'], 8, 0.8, 14, 1.6, 17, 2.2] } },
  ],
  'generated.parcels': [
    { id: 'parcels-ui-fill', type: 'fill', paint: { 'fill-color': ['case', ['==', ['get', 'has_frontage'], false], '#ef4444', '#22c55e'], 'fill-opacity': 0.09 } },
    { id: 'parcels-ui-line', type: 'line', paint: { 'line-color': ['case', ['==', ['get', 'has_frontage'], false], '#dc2626', '#15803d'], 'line-width': ['interpolate', ['linear'], ['zoom'], 10, 0.6, 15, 1.2, 18, 1.8] } },
  ],
  'generated.buildings': [
    { id: 'generated-buildings-ui-fill', type: 'fill', paint: { 'fill-color': ['match', ['get', 'archetype'], 'detached', '#f59e0b', 'point', '#f97316', 'bar', '#2563eb', 'perimeter', '#7c3aed', 'courtyard', '#0d9488', 'public', '#dc2626', 'commercial', '#ca8a04', '#64748b'], 'fill-opacity': 0.48 } },
    { id: 'generated-buildings-ui-line', type: 'line', paint: { 'line-color': ['match', ['get', 'building_use'], 'residential', '#166534', 'mixed', '#1d4ed8', 'public', '#b91c1c', 'commercial', '#a16207', '#334155'], 'line-width': ['interpolate', ['linear'], ['zoom'], 10, 0.6, 15, 1.2, 18, 2] } },
  ],
  'generated.roads': [
    { id: 'roads-ui-generated-line', type: 'line', paint: { 'line-color': roadColors, 'line-width': ['match', ['get', 'road_class'], 'arterial', 4.2, 'collector', 3.2, 'local', 2.2, 2.6], 'line-opacity': 0.92 } },
  ],
  'run.existing_facilities': [
    { id: 'infrastructure-existing-fill', type: 'fill', paint: { 'fill-color': '#64748b', 'fill-opacity': 0.28 }, filter: and(existing, polygons) },
    { id: 'infrastructure-existing-line', type: 'line', paint: { 'line-color': '#475569', 'line-width': 2, 'line-opacity': 0.9 }, filter: existing },
    { id: 'infrastructure-existing-point', type: 'circle', paint: { 'circle-color': '#475569', 'circle-radius': ['interpolate', ['linear'], ['zoom'], 8, 4, 15, 7], 'circle-stroke-color': '#f8fafc', 'circle-stroke-width': 1.5 }, filter: and(existing, points) },
  ],
  'generated.facilities': [
    { id: 'infrastructure-generated-fill', type: 'fill', paint: { 'fill-color': facilityColors, 'fill-opacity': 0.48 }, filter: and(generated, polygons) },
    { id: 'infrastructure-generated-line', type: 'line', paint: { 'line-color': facilityColors, 'line-width': 2.2, 'line-opacity': 0.95 }, filter: generated },
    { id: 'infrastructure-generated-point', type: 'circle', paint: { 'circle-color': facilityColors, 'circle-radius': ['interpolate', ['linear'], ['zoom'], 8, 5, 15, 8], 'circle-stroke-color': '#ffffff', 'circle-stroke-width': 2 }, filter: and(generated, points) },
  ],
  'generated.infrastructure_demand': [
    { id: 'infrastructure-demand-fill', type: 'fill', paint: { 'fill-color': '#f97316', 'fill-opacity': 0.42 } },
    { id: 'infrastructure-demand-line', type: 'line', paint: { 'line-color': '#9a3412', 'line-width': 1.1, 'line-opacity': 0.72 } },
  ],
  'validation.violations': [
    { id: 'validation-violations-fill', type: 'fill', paint: { 'fill-color': severityColors, 'fill-opacity': 0.24 }, filter: polygons },
    { id: 'validation-violations-outline', type: 'line', paint: { 'line-color': severityColors, 'line-width': 3, 'line-opacity': 0.95 }, filter: polygons },
    { id: 'validation-violations-line', type: 'line', paint: { 'line-color': severityColors, 'line-width': 4, 'line-opacity': 0.95 }, filter: lines },
    { id: 'validation-violations-point', type: 'circle', paint: { 'circle-color': severityColors, 'circle-radius': 7, 'circle-stroke-color': '#ffffff', 'circle-stroke-width': 1.5 }, filter: points },
  ],
}

export const SOURCE_MAP_LEGEND: ReadonlyArray<{
  key: SourceLayerKey
  layerId: LayerId
  label: string
  color: string
}> = [
  { key: 'boundary', layerId: 'project.boundary', label: 'Граница проекта', color: '#f59e0b' },
  { key: 'roads', layerId: 'source.roads', label: 'Дороги', color: '#334155' },
  { key: 'buildings', layerId: 'source.buildings', label: 'Здания', color: '#d97706' },
  { key: 'water', layerId: 'source.water', label: 'Вода', color: '#0ea5e9' },
  { key: 'constraints', layerId: 'source.constraints', label: 'Ограничения', color: '#dc2626' },
  { key: 'facilities', layerId: 'source.facilities', label: 'Исходная инфраструктура', color: '#475569' },
  { key: 'landuse', layerId: 'source.landuse', label: 'Землепользование', color: '#65a30d' },
]
