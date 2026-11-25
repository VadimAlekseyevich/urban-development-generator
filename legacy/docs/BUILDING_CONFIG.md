# Building archetype configuration

S08-T01 introduces the immutable configuration contract for generated building archetypes.
It does **not** place building geometry.

## Canonical archetypes

The core catalog exposes these stable codes:

- `detached`
- `point`
- `bar`
- `perimeter`
- `courtyard`
- `public`
- `commercial`

A project config may use any non-empty subset. Not every functional zone has to allow every
archetype.

Each `BuildingArchetypeConfig` declares:

- the archetype code;
- a later footprint strategy token (`rectangular_point`, `bar`, `perimeter`,
  or `courtyard`);
- candidate placement scope (`parcel`, `block`, or `parcel_or_block`);
- canonical allowed `ZoneClass` values;
- a positive selection weight for later seeded archetype selection.

`BuildingConfig` is versioned, canonicalizes archetype order, rejects duplicates, exposes
zone eligibility lookup, and produces a stable SHA-256 content fingerprint.

## Explicit scope boundary

T01 contains no geometry placement, setbacks, buildable-envelope evaluation, slope logic,
inter-building gap, coverage/FAR convergence, floors, use assignment, or GFA calculation.
Those remain in S08-T02 through S08-T11.
