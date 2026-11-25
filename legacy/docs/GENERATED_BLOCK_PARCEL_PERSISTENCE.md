# Generated block / planning-parcel persistence (S07-T10)

S07-T10 persists the final S07 block geometry and the optional simplified planning parcels as
run-scoped generated entities. This is a persistence boundary only; block/parcel map delivery and
inspection belong to S07-T11.

## Inputs

`SqlAlchemyGeneratedBlockParcelWriter.replace(...)` consumes:

- a `GenerationRun` UUID;
- the S07-T07 `BlockZoneAssociationResult`;
- the S07-T09 `ParcelSubdivisionResult`.

Both results and every planning parcel must use the run's metric `working_srid`. The subdivision
must contain exactly one decision per persisted T07 block and its decision parcel IDs must match
its actual output parcels.

## GeneratedBlock contract

Each new T10 block row stores:

- deterministic database `id = UUID5(run_id, core block key)`;
- `run_id`;
- polygon geometry in the run working CRS;
- canonical core `block_key`;
- optional FK `zone_id -> generated_zones.id`;
- metric `area_m2`;
- explicit T07 `association_status`;
- provenance/diagnostics in `attributes_json`, including T05/T06 lineage, overlap diagnostics and
  the T09 subdivision decision.

Legacy rows created before T10 remain schema-valid; the new semantic columns are nullable at the
migration boundary. The T10 writer itself always writes the complete new block contract.

## GeneratedParcel contract

Each T09 planning parcel is persisted with:

- deterministic database `id = UUID5(run_id, core parcel key)`;
- `run_id`;
- canonical `parcel_key`;
- FK `block_id -> generated_blocks.id`;
- optional FK `zone_id -> generated_zones.id`;
- parcel polygon geometry;
- metric `area_m2`, `buildable_area_m2` and `frontage_m`;
- polygonal `buildable_geometry` in the same working CRS;
- non-cadastral semantics, block key, zone class, buildable ratio and road-backed frontage details
  in `attributes_json`.

`GeneratedParcel` remains a procedural planning lot. Persistence does not turn it into a legal or
cadastral parcel.

## Zone references

An `ASSOCIATED` block must carry a T07 zone ID that parses as the UUID of an already persisted
`GeneratedZone`. Before replacing any rows, the writer verifies that every referenced zone:

1. exists;
2. belongs to the same generation run;
3. has the same zone class as the core relation.

Planning parcels must preserve the same zone identity/class as their parent block. Unassociated
blocks may be persisted without a zone FK and do not produce T09 parcels under the simplified
subdivision policy.

The existing generated-zone writer replaces unfinished-run zone rows. If zones are intentionally
rewritten after T10, downstream T07/T09/T10 outputs must be recomputed and persisted again; stage
ordering remains zones -> association/subdivision -> block/parcel persistence.

## Atomic replacement and retries

The writer locks the generation run row and rejects replacement when the run is already
`succeeded`. For an unfinished run it validates all inputs and zone references before deleting any
existing block/parcel rows.

Replacement occurs in one database transaction:

1. count existing run-scoped parcel/block rows;
2. delete parcels for the run;
3. delete blocks for the run;
4. bulk-insert blocks in bounded chunks;
5. bulk-insert parcels in bounded chunks.

A failed validation or SQL transaction therefore cannot leave a partial block/parcel set.
Deterministic UUID5 IDs preserve row identity across valid retries of the same run/core keys.

## Indexes

Existing `(run_id, id)` and GiST geometry indexes from S02 remain in place. T10 adds semantic
indexes required by later map/query stages:

- unique `(run_id, block_key)`;
- block `(run_id, zone_id)`;
- block `(run_id, association_status)`;
- unique `(run_id, parcel_key)`;
- parcel `(run_id, block_id)`;
- parcel `(run_id, zone_id)`;
- GiST on parcel `buildable_geometry`.

## Explicit boundary

S07-T10 does **not** add HTTP endpoints, bbox/GeoJSON query repositories, React layer toggles or
inspectors. Those are S07-T11. Broad geometry properties and reference-scale performance coverage
remain S07-T12.
