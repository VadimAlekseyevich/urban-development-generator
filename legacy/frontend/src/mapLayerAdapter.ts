/** MapLibre transport adapter for the declarative catalog. No HTTP/read-model logic.
 * GeoJSON uses a current viewport collection, MVT uses exact owner tile URLs,
 * and suitability uses georeferenced image coordinates from artifact metadata.
 */
import type { LayerSpecification, Map as MapLibreMap } from 'maplibre-gl'
import { EMPTY_FEATURE_COLLECTION, type GeoJsonFeatureCollection } from './sourceLayers'
import {
  LAYER_REGISTRY,
  layerDefinition,
  mvtTileUrl,
  type LayerId,
  type LayerInstance,
} from './layerRegistry'
import { MAP_LAYER_STYLES, type LayerStylePart } from './layerStyles'
import type { SuitabilityImageCoordinates } from './suitabilityLayer'

function nextLayerId(map: MapLibreMap, id: LayerId): string | undefined {
  const index = LAYER_REGISTRY.findIndex((entry) => entry.id === id)
  for (const entry of LAYER_REGISTRY.slice(index + 1)) {
    for (const style of MAP_LAYER_STYLES[entry.id as LayerId]) {
      if (map.getLayer(style.id)) return style.id
    }
  }
  return undefined
}
function addStyles(
  map: MapLibreMap,
  id: LayerId,
  sourceId: string,
  options: { visible?: boolean; mvt?: boolean } = {},
): readonly string[] {
  const definition = layerDefinition(id)
  const layerIds: string[] = []
  for (const part of MAP_LAYER_STYLES[id]) {
    const layerId = options.mvt ? `${part.id}--mvt--${sourceId}` : part.id
    if (!map.getLayer(layerId)) {
      const spec: Record<string, unknown> = {
        id: layerId,
        type: part.type,
        source: sourceId,
        paint: part.paint,
        layout: {
          visibility: (options.visible ?? definition.defaultVisible) ? 'visible' : 'none',
        },
      }
      if (part.filter) spec.filter = part.filter
      if (options.mvt) spec['source-layer'] = id
      map.addLayer(spec as unknown as LayerSpecification, nextLayerId(map, id))
    }
    layerIds.push(layerId)
  }
  return layerIds
}
/** Stable existing source IDs keep the current MapLibre inspector and panel bindings. */
export function installCatalogGeoJson(
  map: MapLibreMap,
  id: LayerId,
  data: GeoJsonFeatureCollection = EMPTY_FEATURE_COLLECTION,
): readonly string[] {
  const definition = layerDefinition(id)
  if (definition.deliveryKind === 'artifact_image') throw new Error('Raster is not GeoJSON')
  if (!map.getSource(definition.sourceId)) {
    map.addSource(definition.sourceId, { type: 'geojson', data })
  }
  return addStyles(map, id, definition.sourceId)
}
export function layerStyleIds(id: LayerId): readonly string[] {
  return MAP_LAYER_STYLES[id].map((part: LayerStylePart) => part.id)
}
export function removeCatalogImage(map: MapLibreMap): void {
  const definition = layerDefinition('analysis.suitability')
  for (const style of MAP_LAYER_STYLES['analysis.suitability']) {
    if (map.getLayer(style.id)) map.removeLayer(style.id)
  }
  if (map.getSource(definition.sourceId)) map.removeSource(definition.sourceId)
}
/** Suitability preview is an image source, not a pretend bbox/GeoJSON/MVT layer. */
export function installCatalogImage(
  map: MapLibreMap,
  url: string,
  coordinates: SuitabilityImageCoordinates,
  visible = true,
  opacity = 0.62,
): void {
  if (!Number.isFinite(opacity) || opacity < 0 || opacity > 1) {
    throw new Error('Raster opacity must be 0..1')
  }
  if (!coordinates.every((coordinate) =>
    coordinate.length === 2 && coordinate.every(Number.isFinite)
    && coordinate[0] >= -180 && coordinate[0] <= 180
    && coordinate[1] >= -90 && coordinate[1] <= 90
  )) throw new Error('Raster preview requires four WGS84 image corners')
  removeCatalogImage(map)
  const descriptor = layerDefinition('analysis.suitability')
  map.addSource(descriptor.sourceId, { type: 'image', url, coordinates })
  const layerId = addStyles(map, 'analysis.suitability', descriptor.sourceId, { visible })[0]
  map.setPaintProperty(layerId, 'raster-opacity', opacity)
}
export type MvtMount = Readonly<{
  instanceKey: string
  sourceId: string
  layerIds: readonly string[]
  remove: () => void
}>
/** Tile style and source IDs include exact run/version identity, avoiding cross-run reuse.
 * Does not claim publication or availability: all reads are authorized by the backend.
 */
export function installCatalogMvt(
  map: MapLibreMap,
  apiBase: string,
  instance: LayerInstance,
  featureLimit = 500,
): MvtMount {
  const tiles = [
    mvtTileUrl(apiBase, instance, '{z}', '{x}', '{y}', featureLimit),
  ]
  const sourceId = `catalog-mvt:${instance.instanceKey}:${featureLimit}`
  if (!map.getSource(sourceId)) {
    map.addSource(sourceId, {
      type: 'vector',
      tiles,
      minzoom: 0,
      maxzoom: 16,
    })
  }
  const id = instance.definition.id as LayerId
  const layerIds = addStyles(map, id, sourceId, { mvt: true })
  return {
    instanceKey: instance.instanceKey, sourceId, layerIds,
    remove: () => {
      for (const layerId of layerIds) {
        if (map.getLayer(layerId)) map.removeLayer(layerId)
      }
      if (map.getSource(sourceId)) map.removeSource(sourceId)
    },
  }
}
