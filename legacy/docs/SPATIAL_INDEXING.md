# Spatial indexing policy

S02-T10 фиксирует минимальный набор access indexes для пространственных сущностей до появления конкретных repository/API query implementations.

## Query scopes

Canonical source layers всегда читаются в scope конкретного `DatasetVersion`. Generated layers всегда читаются в scope конкретного `GenerationRun`. Пространственная выдача дополнительно ограничивается bbox и bounded `limit`; для детерминированной пагинации используется UUID `id` как стабильный cursor/order key.

Целевые формы запросов:

```sql
SELECT ...
FROM source_roads
WHERE dataset_version_id = :dataset_version_id
  AND geometry && :bbox
  AND id > :cursor
ORDER BY id
LIMIT :limit;
```

```sql
SELECT ...
FROM generated_buildings
WHERE run_id = :run_id
  AND geometry && :bbox
  AND id > :cursor
ORDER BY id
LIMIT :limit;
```

## Index set

Для каждой таблицы `source_roads`, `source_buildings`, `source_landuse`, `source_water`, `source_facilities`, `source_constraints` создаются:

- B-tree `(dataset_version_id, id)` для scope filtering и keyset pagination;
- GiST `(geometry)` для bbox/spatial predicates.

Для каждой таблицы `generated_zones`, `generated_roads`, `generated_blocks`, `generated_parcels`, `generated_buildings`, `generated_infrastructure` создаются:

- B-tree `(run_id, id)` для run-scoped filtering и keyset pagination;
- GiST `(geometry)` для bbox/spatial predicates.

PostgreSQL может комбинировать scoped B-tree и GiST через bitmap plans. `btree_gist` сознательно не требуется: scope и geometry остаются независимыми indexes, что не вводит дополнительную extension dependency.

## Existing indexes retained

S02-T10 не дублирует уже существующие access paths: `projects.boundary` имеет GiST; `datasets.project_id`, `dataset_versions.dataset_id`, `generation_runs.project_id/status` имеют B-tree indexes; `(dataset_id, version)` и `(run_id, dataset_version_id)` покрываются unique/primary-key constraints, а reverse lookup по `generation_run_dataset_versions.dataset_version_id` уже индексирован.

JSONB, classification и metric-specific indexes пока не добавляются. Их следует вводить только вместе с реальным query pattern и `EXPLAIN`/benchmark evidence, чтобы ingestion и generation не платили write-cost за speculative indexes.

Миграция: `0011_spatial_indexes`.
