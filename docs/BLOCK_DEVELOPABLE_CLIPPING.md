# S07-T02 — Developable block clipping

`core.urban_generator.blocks.DevelopableBlockClipper` is the geometry boundary between raw road polygonization (`S07-T01`) and later block analysis.

## Contract

Input:

- immutable `BlockPolygonizationResult` from S07-T01;
- `BlockDevelopableArea` with project boundary, developable mask and explicit metric `working_srid`;
- zero or more named `BlockHardConstraintLayer` values containing polygonal HARD exclusions in the same working CRS.

Processing:

1. intersect project boundary and developable mask;
2. intersect every source candidate block with that effective area;
3. query HARD exclusions through one `STRtree`;
4. subtract only positive-area exclusion hits;
5. keep every positive-area polygon fragment and normalize ordering deterministically.

A source candidate can therefore produce zero, one or multiple `DevelopableBlockCandidate` values. Output IDs are regenerated deterministically, while `source_block_id` and `source_fragment_index` retain provenance.

## Bounded work

The clipper exposes explicit limits for:

- input candidate blocks;
- total HARD constraint geometries;
- STRtree candidates per source block;
- total output fragments.

It does not perform a full block-by-constraint N×M scan.

## Deliberate S07-T02 boundaries

This work item does **not** implement:

- block area/perimeter/compactness/aspect/holes metrics (`S07-T03`);
- frontage/access validation (`S07-T04`);
- oversized block splitting (`S07-T05`);
- minimum-area or sliver drop/merge policy (`S07-T06`);
- zone association, parcels, persistence, API or UI.

Small positive fragments are intentionally retained so later sliver policy is explicit and diagnostic rather than silently embedded in clipping.

Raster hard masks are not sampled directly by this vector operation. Any raster-derived HARD exclusion that must affect block geometry has to be materialized as the corresponding polygonal developable/exclusion geometry before this contract is called.
