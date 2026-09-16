# Block zone association

S07-T07 attaches the cleaned S07-T06 block geometry to an explicit functional-zone identity without repeating or hiding a downstream spatial join.

## Inputs

`BlockZoneAssociator` consumes:

- `SliverCleanupResult` from S07-T06;
- immutable `BlockZoneReference` values containing `zone_id`, canonical `ZoneClass`, polygonal geometry, and the working SRID.

`zone_id` is deliberately a core string rather than a storage-specific UUID. A persisted generated zone may pass `str(GeneratedZone.id)` while a fixed/existing zoning source may pass its own canonical feature ID. Core therefore remains independent from SQLAlchemy and database models.

All block and zone geometry must use the same projected metric working CRS.

## Association semantics

The associator uses one bounded `STRtree` over zone geometry and calculates exact positive-area intersections only for spatial candidates.

A block is `ASSOCIATED` only when exactly one positive-overlap zone covers the complete block area within the metric tolerance. The result then carries both `zone_id` and `zone_class`.

Other outcomes remain explicit rather than being guessed:

- `NO_OVERLAP` — there is no positive-area zone overlap; boundary-only touching is not an association;
- `PARTIAL_OVERLAP` — one or more zones overlap the block, but none uniquely covers the full block;
- `AMBIGUOUS_FULL_COVERAGE` — at least one zone fully covers the block while another positive overlap also exists.

The implementation intentionally does **not** select a dominant zone by largest area. A block crossing a zoning boundary must stay unresolved so later stages do not silently inherit an arbitrary label.

Very small numerical intersections at or below the area tolerance are treated as zero-area contact.

## Determinism and diagnostics

Zones are canonicalized by `zone_id`; cleaned blocks are processed by stable `block_id`. Positive-overlap zone IDs are sorted and unique.

`BlockZoneAssociationDiagnostics` records:

- block and zone counts;
- associated, no-overlap, partial-overlap, and ambiguous block counts;
- spatial candidate-pair count;
- exact positive-overlap pair count.

Inputs are bounded by configurable maximum block count, zone count, and zone candidates per block. Duplicate block or zone IDs are rejected.

## Persistence boundary

T07 creates a persistence-ready core relation but does not modify `generated_blocks` or introduce database foreign keys. Block/parcel persistence belongs to S07-T10. That stage can store the T07 `zone_id` directly instead of recomputing a spatial join.

## Scope boundary

S07-T07 does not define parcels, subdivision, generated-block persistence, API endpoints, or UI. Those remain S07-T08 through S07-T11.
