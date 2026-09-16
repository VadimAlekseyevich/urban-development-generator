# Simplified parcel subdivision

S07-T09 creates optional non-cadastral planning lots for **suitable, explicitly associated residential blocks**. It consumes the S07-T07 block→zone relation, the S07-T08 `PlanningParcel` domain model, and the noded S06 road graph.

## Eligibility

A block is considered only when T07 reports `ASSOCIATED` and the associated `ZoneClass` is `RESIDENTIAL`. Unassociated, ambiguous, partial-overlap and non-residential blocks deliberately produce no parcels.

The v1 simplified algorithm is conservative. A residential block is also skipped when it:

- contains polygon holes;
- is smaller than the configured minimum parcel area;
- has no positive-length road overlap on its boundary;
- has insufficient dominant frontage;
- has a dominant frontage that is too curved for the configured linearity threshold;
- cannot be split into a complete valid set of frontage lots;
- would create a fragment below the minimum parcel area or minimum frontage.

Every skipped block has a typed `ParcelSubdivisionSkipReason`. The algorithm never returns a partial parcel set for a failed block.

## Policy

`ParcelSubdivisionPolicy` defines metric, configuration-driven controls:

- `target_frontage_m`;
- `minimum_frontage_m`;
- `minimum_parcel_area_m2`;
- `max_parcels_per_block`;
- `minimum_frontage_linearity_ratio`.

No legal/cadastral dimensions are inferred from these values. They are procedural scenario parameters.

## Frontage-based split

Road edges are indexed once with an `STRtree`; candidates are bounded per block. A road counts as frontage only when its geometry has a positive-length overlap with the block boundary. Transverse road crossings therefore do not become frontage.

Duplicate/overlapping road geometry is de-duplicated deterministically before frontage length is used. The longest unique frontage piece is selected as the dominant frontage. For a sufficiently straight dominant frontage, the requested lot count is bounded jointly by target frontage, minimum frontage, minimum parcel area, and `max_parcels_per_block`.

For more than one lot, cut lines are placed at equal positions along the dominant frontage and extended perpendicular to its chord through the block. The split is accepted only when it produces exactly the expected number of positive-area polygons and conserves the complete block area.

Each resulting polygon then re-evaluates exact road-backed frontage. Every lot must satisfy the configured minimum frontage and minimum area.

## Planning parcel output

Accepted fragments become S07-T08 `PlanningParcel` values with:

- deterministic `parcel:########` IDs;
- parent `block_id`;
- inherited explicit residential `zone_id` and `zone_class`;
- exact road-backed frontage segments;
- parcel geometry as the baseline `buildable_envelope`.

The baseline envelope deliberately means “not further reduced by T09”. T09 does not implement building setbacks or later building-stage constraint refinement.

A suitable small residential block may produce one planning parcel without an actual geometric split. This preserves the optional-parcel semantics while keeping one consistent downstream parcel model.

## Bounded behavior and diagnostics

The stage has explicit limits for input blocks, road edges, road candidates per block, output parcels and parcels per block. `ParcelSubdivisionDiagnostics` records eligible/parceled/skipped counts, single-vs-subdivided block counts, parcel count, road candidate work, frontage overlaps and total parceled area.

## Scope boundary

S07-T09 does not persist blocks/parcels, expose API endpoints, render UI, implement cadastral logic, or perform broad property/performance suites. Persistence belongs to S07-T10; UI belongs to S07-T11; broader property/performance coverage belongs to S07-T12.
