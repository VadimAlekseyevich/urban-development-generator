import { useSyncExternalStore } from 'react'

import { LAYER_REGISTRY, type LayerId } from './layerRegistry'

export type LayerVisibilitySnapshot = Readonly<Record<LayerId, boolean>>
export type LayerVisibilityUpdater = boolean | ((current: boolean) => boolean)

function initialVisibility(): LayerVisibilitySnapshot {
  return Object.freeze(Object.fromEntries(
    LAYER_REGISTRY.map((definition) => [
      definition.id,
      definition.defaultVisible,
    ]),
  ) as Record<LayerId, boolean>)
}

let snapshot = initialVisibility()
const listeners = new Set<() => void>()

export function getLayerVisibilitySnapshot(): LayerVisibilitySnapshot {
  return snapshot
}

export function subscribeLayerVisibility(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export function setLayerVisible(id: LayerId, updater: LayerVisibilityUpdater): void {
  const current = snapshot[id]
  const next = typeof updater === 'function' ? updater(current) : updater
  if (current === next) return
  snapshot = Object.freeze({ ...snapshot, [id]: next })
  for (const listener of listeners) listener()
}

export function resetLayerVisibility(): void {
  snapshot = initialVisibility()
  for (const listener of listeners) listener()
}

export function useLayerVisibilitySnapshot(): LayerVisibilitySnapshot {
  return useSyncExternalStore(
    subscribeLayerVisibility,
    getLayerVisibilitySnapshot,
    getLayerVisibilitySnapshot,
  )
}

export function useLayerVisibility(
  id: LayerId,
): readonly [boolean, (updater: LayerVisibilityUpdater) => void] {
  const current = useLayerVisibilitySnapshot()[id]
  return [current, (updater) => setLayerVisible(id, updater)] as const
}
