# Road classification — S06-T12

`RuleBasedRoadClassifier` is the bounded, deterministic classification stage for road records after baseline connectivity and rule-based growth. It keeps source/existing road classes untouched and assigns the canonical generated classes `local`, `collector`, or `arterial` only to generated roads.

## Existing-class preservation

Existing roads are represented by `RoadClassificationOrigin.EXISTING` and must provide `existing_class`. The classifier copies that string to the result exactly; it does not normalize, map, lowercase, or replace source values. `generated_class` remains `None`, and provenance is `EXISTING_PRESERVED`.

This allows source schemas such as `primary_link`, provider-specific values, or previously curated classes to survive S06-T12 unchanged.

## Generated-road rules

Generated roads use the canonical `GeneratedRoadClass` enum. The default v1 rules are configurable through `RoadClassificationPolicy`:

- `collector_min_length_m = 500`;
- `arterial_min_length_m = 2,000`;
- MST/baseline generated links have a minimum class of `collector`;
- S06-T11 `COLLECTOR` growth intent has a minimum class of `collector`.

Length rules are evaluated first for the highest class: a generated link at or above the arterial threshold is `arterial`; otherwise the collector threshold can promote it to `collector`. Baseline or growth-intent floors can then promote a shorter generated link. Floors are themselves configurable and may be set to `local`, `collector`, or `arterial`.

S06-T11 intent remains input provenance rather than an authoritative class. For example, a `LOCAL` growth link can still become `collector` or `arterial` because of configured length thresholds.

## Input contract

`RoadClassificationSubject` separates three stable origins:

- `EXISTING`: requires `existing_class`; length is optional; growth intent is forbidden;
- `BASELINE_GENERATED`: requires positive metric `length_m`; existing class and growth intent are forbidden;
- `GROWTH_GENERATED`: requires positive metric `length_m` plus S06-T11 `RoadGrowthIntent`.

Subjects are sorted by `road_id`, duplicate ids are rejected, and the pass is bounded by `max_subjects` (default 1,000,000).

## Diagnostics and provenance

Every classified road records a stable `RoadClassificationReason`, including preservation, local default, collector-by-length, collector-intent floor, baseline floor, or arterial-by-length. Aggregate diagnostics count preserved existing roads and each generated canonical class.

## Scope boundary

S06-T12 classifies road records; it does not change geometry, connectivity, ownership/fixed semantics, source classes, or persistence. Final network validation (connectivity, dead ends, forbidden crossings, invalid geometry) remains S06-T13.
