# Generated zone persistence

S05-T08 persists the final functional-zoning result as run-scoped `GeneratedZone` rows.

## Stored fields

`generated_zones` keeps the generic generated-entity columns (`id`, `run_id`, `geometry`, `attributes_json`, `created_at`) and adds typed zoning fields. `id` is the canonical deterministic UUID5 derived from `(run_id, cell_index, seed_index)` by `generated_zone_uuid(...)`, so downstream core stages can reference a generated zone before persistence:

- `zone_class` — one of `residential`, `mixed`, `public`, `recreation`;
- `area_m2` — positive metric area in the generation run working CRS;
- `diagnostics_json` — per-zone diagnostics and provenance.

`diagnostics_json` contains:

- partition `cell_index` and `seed_index`;
- suitability score;
- whether the partition cell needed validity repair;
- the exact shared-constraint `ValidationReport` results for that zone;
- zoning config version/fingerprint;
- assignment strategy version;
- constraint stage/evaluator version.

Constraint pass/fail semantics are not recomputed by persistence. The writer serializes the S05-T07 shared-engine result exactly.

## Transaction and retry semantics

`SqlAlchemyGeneratedZoneWriter.replace(...)` replaces all generated zones owned by one unfinished `GenerationRun` in one database transaction.

A retry therefore does not append duplicate zone rows:

1. lock the target run row;
2. validate run working SRID against the partition;
3. validate partition/assignment/constraint-result alignment;
4. delete previous zones for that run;
5. insert the complete replacement set in bounded chunks.

A retry preserves the same generated-zone IDs for the same run and aligned partition cells; persistence does not invent a new identity.

If validation or insertion fails, the transaction rolls back and the previous committed zone set remains intact.

Successful runs are immutable. The writer rejects replacement before deletion, and the existing database generated-entity trigger independently prevents insert/update/delete for a `succeeded` run.

## Geometry / CRS

Partition cells are persisted as `MULTIPOLYGON`, converting a single `Polygon` without changing its shape. Geometry is tagged with the run `working_srid`; the existing database trigger requires the geometry SRID to match the owning run.

`area_m2` is taken from the already validated partition result and is never calculated in EPSG:4326.

## Migration

Migration `0012_generated_zone_persistence` adds the typed fields and database check constraints. For legacy generic rows it derives `area_m2` from geometry and can migrate `zone_class` when the row already carries a valid `attributes_json.zone_class`. It fails rather than inventing a functional class when legacy rows have no valid class.

Downgrade copies the typed T08 values back into `attributes_json` before dropping the specialized columns, avoiding silent semantic data loss.

## Scope boundary

S05-T08 does not expose generated zones over HTTP and does not render them in the frontend. That is S05-T09 (`Zoning UI vertical slice`).
