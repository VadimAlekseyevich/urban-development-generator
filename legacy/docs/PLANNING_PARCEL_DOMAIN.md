# Planning parcel domain model

S07-T08 defines the parcel object consumed by later subdivision/building stages. It is deliberately a **planning lot**, not a cadastral or legal land parcel.

The normative semantics string is `planning_lot_non_cadastral`.

## Why this model exists

A `PlanningParcel` is a lightweight geometry/attribute contract for procedural building placement. It lets downstream stages reason about:

- parent generated block identity;
- optional explicit functional-zone relation from S07-T07;
- parcel polygon geometry;
- road-backed frontage geometry and total frontage length;
- the parcel's polygonal buildable envelope.

The model does not represent ownership, title, easements, cadastral numbers, legal boundaries, valuation, or any other cadastral/legal concept.

## Public contract

`PlanningParcel` contains:

- `parcel_id` — stable parcel identity inside the generated result;
- `block_id` — parent generated block identity;
- `working_srid` — projected metre-based working CRS;
- `geometry` — one valid positive-area `Polygon`;
- `buildable_envelope` — valid positive-area `Polygon` or `MultiPolygon` contained by the parcel;
- `frontages` — immutable, canonically ordered `ParcelFrontageSegment` values;
- optional `zone_id` + `zone_class` pair copied from the explicit S07-T07 relation when available.

`zone_id` and `zone_class` are all-or-nothing: the domain model never exposes a half-populated zone relation.

## Frontage

Each `ParcelFrontageSegment` contains a stable `road_id` and a positive-length `LineString`/`MultiLineString` in the parcel working CRS.

Every frontage segment must lie on the parcel boundary within the numerical length tolerance. Frontages are canonically ordered by road ID and geometry bytes and must be unique. The model exposes derived total frontage length and unique road IDs.

The number of frontage segments on one parcel is hard-bounded by `MAX_PLANNING_PARCEL_FRONTAGES` so validation cannot grow without limit.

## Buildable envelope

The buildable envelope is required and must remain inside the parcel up to a small numerical area tolerance. It may be multipart when setbacks/constraints leave disconnected buildable regions.

The model exposes raw parcel area, buildable area, and buildable-area ratio. These are metric values because geographic CRS is rejected by the working-CRS contract.

T08 does not calculate the envelope. A producing stage must supply it according to its own rules; T08 only validates and carries the result.

## Optional parcels

The project specification allows some development types to use block-level building placement without parcels. This is represented by the **absence of `PlanningParcel` objects**, not by a fake parcel type or a cadastral surrogate.

## Scope boundary

S07-T08 defines only the immutable domain model. It does not subdivide blocks, choose target lot sizes, create frontage cuts, persist generated parcels, expose HTTP endpoints, or render UI.

Simplified frontage-based subdivision belongs to S07-T09. Persistence belongs to S07-T10.
