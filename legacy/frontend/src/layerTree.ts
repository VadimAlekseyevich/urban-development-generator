import {
  LAYER_REGISTRY,
  layerInstancesForContext,
  type LayerId,
  type LayerInstance,
  type LayerSelection,
  type LayerSourceKind,
} from './layerRegistry'
import type { LayerVisibilitySnapshot } from './layerVisibility'

export type LayerTreeGroupId = LayerSourceKind

export type LayerTreeNode = Readonly<{
  id: LayerId
  label: string
  instance: LayerInstance | null
  visible: boolean
  sourceKind: LayerSourceKind
  ownerScope: string
  deliveryKind: string
}>

export type LayerTreeGroup = Readonly<{
  id: LayerTreeGroupId
  label: string
  nodes: readonly LayerTreeNode[]
}>

const GROUP_LABELS: Readonly<Record<LayerTreeGroupId, string>> = {
  analysis: 'Analysis',
  source: 'Source / fixed',
  generated: 'Generated',
  validation: 'Validation',
}

const LAYER_LABELS: Readonly<Record<LayerId, string>> = {
  'analysis.suitability': 'Suitability',
  'project.boundary': 'Project boundary',
  'source.landuse': 'Land use',
  'source.water': 'Water',
  'source.constraints': 'Constraints',
  'source.facilities': 'Source facilities',
  'source.buildings': 'Source buildings',
  'source.roads': 'Source roads',
  'generated.zones': 'Generated zones',
  'generated.demography': 'Demography',
  'generated.blocks': 'Generated blocks',
  'generated.parcels': 'Planning parcels',
  'generated.buildings': 'Generated buildings',
  'generated.roads': 'Generated roads',
  'run.existing_facilities': 'Run existing facilities',
  'generated.facilities': 'Generated facilities',
  'generated.infrastructure_demand': 'Infrastructure demand',
  'validation.violations': 'Violations',
}

export function layerOwnerCaption(instance: LayerInstance | null): string {
  if (!instance) return 'owner not selected'
  const owner = instance.owner
  if (owner.scope === 'project') return `project ${owner.projectId?.slice(0, 8)}`
  if (owner.scope === 'dataset_version') {
    return `version ${owner.datasetVersionId?.slice(0, 8)}`
  }
  if (owner.scope === 'run') return `run ${owner.runId?.slice(0, 8)}`
  return `artifact ${owner.artifactId?.slice(0, 8)}`
}

export function buildLayerTree(
  selection: LayerSelection,
  visibility: LayerVisibilitySnapshot,
): readonly LayerTreeGroup[] {
  const instances = new Map(
    layerInstancesForContext(selection).map((instance) => [
      instance.definition.id,
      instance,
    ]),
  )
  const nodes = LAYER_REGISTRY.map((definition): LayerTreeNode => {
    const id = definition.id as LayerId
    return {
      id,
      label: LAYER_LABELS[id],
      instance: instances.get(definition.id) ?? null,
      visible: visibility[id],
      sourceKind: definition.sourceKind,
      ownerScope: definition.ownerScope,
      deliveryKind: definition.deliveryKind,
    }
  })
  const order: readonly LayerTreeGroupId[] = [
    'analysis',
    'source',
    'generated',
    'validation',
  ]
  return order.map((id) => ({
    id,
    label: GROUP_LABELS[id],
    nodes: nodes.filter((node) => node.sourceKind === id),
  }))
}
