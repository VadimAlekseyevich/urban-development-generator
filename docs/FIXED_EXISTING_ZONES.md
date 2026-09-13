# Fixed existing zones adapter

S05-T02 adds an explicit immutable contract for existing functional zoning used by the zoning pipeline.

## Snapshot contract

`TerritorySnapshot` now exposes:

- `fixed_zones: tuple[SnapshotLayerRef, ...]`;
- `SnapshotLayerKind.ZONES` for the semantic zoning view.

Every `fixed_zones` reference is fixed source state through the existing `WorldStateContract.fixed_source()` semantics. Existing zoning therefore has no `run_id` owner and is not generated output.

`fixed_zones` is included in `TerritorySnapshot.layer_refs` and counts as existing fixed urban state for expansion-mode semantics.

## Adapter

`FixedExistingZonesAdapter` converts selected canonical `LANDUSE` source layer references into semantic `ZONES` references while preserving the exact opaque `source_ref`.

The adapter:

- accepts only an immutable tuple of `SnapshotLayerRef` values;
- accepts only `LANDUSE` source refs;
- rejects duplicate `source_ref` values;
- returns refs in deterministic `source_ref` order;
- creates new immutable `ZONES` refs;
- never mutates the input refs or source features.

An empty tuple is valid and produces no fixed zones, which keeps the same contract usable for `FROM_SCRATCH` runs and projects without existing zoning.

## Why source landuse is reused

The canonical source schema already persists polygonal land-use data in `source_landuse`. S05-T02 does not add a duplicate source-zoning table. Infrastructure can select a normalized source-landuse dataset that represents existing functional zoning and expose it to core through its opaque layer reference.

The semantic `ZONES` reference tells downstream zoning stages that this selected source layer is fixed existing zoning, while the original `LANDUSE` reference can remain available independently for suitability or other analysis.

## Class mapping boundary

This adapter does **not** infer a functional `ZoneClass` from arbitrary OSM or uploaded land-use labels. Automatic guessing would make fixed zoning dependent on hidden heuristics.

The selected source data must already be semantically identified as existing functional zoning by the calling ingest/application layer. Any explicit source-value-to-`ZoneClass` mapping, if required by a concrete import workflow, must be versioned and tested rather than inferred inside this adapter.

## Mutation boundary

S05-T02 does not update source rows and does not write `GeneratedZone` records. Existing source zoning remains immutable. Generated zoning stays a separate run-scoped concern and will be persisted only in S05-T08.

## Scope

This work item intentionally does not implement:

- zoning seed generation;
- partition geometry;
- zone assignment or region growth;
- generated-zone persistence;
- zoning API/UI.

Those remain in later S05 work items.
