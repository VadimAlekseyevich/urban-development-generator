import {
  buildLayerTree,
  layerOwnerCaption,
} from './layerTree'
import type { LayerId, LayerSelection } from './layerRegistry'
import {
  setLayerVisible,
  useLayerVisibilitySnapshot,
} from './layerVisibility'

type LayerTreeProps = {
  selection: LayerSelection
  counts?: Readonly<Partial<Record<LayerId, string | number>>>
  sourceStatus?: 'idle' | 'loading' | 'ready' | 'error'
  sourceMessage?: string
  sourceTruncated?: boolean
  sourceLimit?: number
  onFitSource?: () => void
  onRefreshSource?: () => void
}

export function LayerTree({
  selection,
  counts = {},
  sourceStatus = 'idle',
  sourceMessage,
  sourceTruncated = false,
  sourceLimit,
  onFitSource,
  onRefreshSource,
}: LayerTreeProps) {
  const visibility = useLayerVisibilitySnapshot()
  const groups = buildLayerTree(selection, visibility)
  const boundCount = groups.flatMap((group) => group.nodes)
    .filter((node) => node.instance !== null).length

  return (
    <section className="panel layer-tree-panel">
      <div className="section-heading section-heading-row">
        <div>
          <p className="section-kicker">S13 · Layer catalog</p>
          <h2>Слои карты</h2>
        </div>
        <span className="badge">{boundCount}/18</span>
      </div>

      <div className="layer-tree">
        {groups.map((group) => (
          <details className="layer-tree-group" key={group.id} open>
            <summary>
              <span>{group.label}</span>
              <span>{group.nodes.length}</span>
            </summary>
            <div className="layer-tree-items">
              {group.nodes.map((node) => {
                const count = counts[node.id]
                const enabled = node.instance !== null
                return (
                  <label
                    className={enabled ? 'layer-tree-row' : 'layer-tree-row layer-tree-row-disabled'}
                    key={node.id}
                    title={node.instance?.instanceKey ?? `Requires ${node.ownerScope} owner`}
                  >
                    <input
                      type="checkbox"
                      checked={node.visible}
                      disabled={!enabled}
                      onChange={(event) => setLayerVisible(node.id, event.target.checked)}
                    />
                    <span className={'layer-kind-dot layer-kind-' + node.sourceKind} />
                    <span className="layer-tree-name">
                      <strong>{node.label}</strong>
                      <small>{layerOwnerCaption(node.instance)} · {node.deliveryKind}</small>
                    </span>
                    <span className="layer-count">{count ?? (enabled ? '·' : '—')}</span>
                  </label>
                )
              })}
            </div>
          </details>
        ))}
      </div>

      {(onFitSource || onRefreshSource) && (
        <div className="button-row">
          <button
            className="button"
            type="button"
            onClick={onFitSource}
            disabled={!selection.projectId}
          >
            Fit source
          </button>
          <button
            className="button"
            type="button"
            onClick={onRefreshSource}
            disabled={!selection.datasetVersionId}
          >
            Обновить source
          </button>
        </div>
      )}
      {sourceMessage && (
        <div className={`load-state load-state-${sourceStatus}`}>{sourceMessage}</div>
      )}
      {sourceTruncated && sourceLimit && (
        <p className="warning-text">
          Один или несколько source-слоёв достигли viewport limit ({sourceLimit}); приблизьте карту.
        </p>
      )}
      <p className="helper-text">
        Недоступный checkbox означает, что для canonical owner scope ещё не выбран
        project, DatasetVersion, общий map run или suitability artifact.
      </p>
    </section>
  )
}
