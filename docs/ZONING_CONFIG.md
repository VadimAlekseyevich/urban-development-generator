# Functional zoning domain/config

S05-T01 defines the algorithm-independent functional zoning contract used by later zoning stages.
It does **not** generate geometry, persist zones, or add a UI.

## Canonical zone classes

The v1 domain contains exactly four functional classes:

- `residential`
- `mixed`
- `public`
- `recreation`

These are semantic codes, not labels tied to one city or jurisdiction.

## Per-class allocation rules

Each `ZoneClassConfig` defines:

- `target_share` — desired fraction of generated developable zoning area, in `0..1`;
- `minimum_area_m2` — minimum generated zone area in square metres.

A `ZoningConfig` must define every canonical class exactly once. Target shares must sum to `1.0`
within a strict floating-point tolerance of `1e-9`. A class may have a zero target share, but its
minimum-area rule remains a positive metric value so later algorithms do not need a second
"disabled minimum" representation.

`minimum_area_m2` is deliberately unit-explicit. Later geometry stages must evaluate it in the
project working metric CRS, never in EPSG:4326.

## Adjacency rules

`ZoneAdjacencyRule` applies to an unordered pair of different zone classes. The supported policies
are:

- `PREFERRED` — adjacency is allowed and desirable;
- `ALLOWED` — adjacency is allowed without preference;
- `DISCOURAGED` — adjacency is allowed but undesirable;
- `FORBIDDEN` — the pair must not touch in an accepted zoning result.

Adjacency is symmetric. Constructing a rule as `(recreation, residential)` is canonicalized to the
same pair as `(residential, recreation)`. Duplicate rules for the same unordered pair are rejected.
Self-adjacency is always `ALLOWED` and cannot be configured explicitly. An unspecified pair also
defaults to `ALLOWED`.

The policy is a domain/config fact only. S05-T01 does not prescribe how future assignment or region
growth algorithms translate `PREFERRED` or `DISCOURAGED` into scoring penalties.

## Determinism and fingerprint

`ZoningConfig` canonicalizes zone definitions in enum order and adjacency rules by canonical pair.
Equivalent configs therefore have the same ordering and SHA-256 `fingerprint` even if callers
supplied their tuples in a different order.

The fingerprint covers:

- config version;
- class codes;
- target shares;
- minimum areas;
- adjacency pair policies.

Changing any of those semantics changes the fingerprint.

## Example

The following values are illustrative only; they are not a planning recommendation for Ryazan or
any other city:

```python
from core.urban_generator.zoning import (
    ZoneAdjacencyPolicy,
    ZoneAdjacencyRule,
    ZoneClass,
    ZoneClassConfig,
    ZoningConfig,
)

config = ZoningConfig(
    version="v1",
    zones=(
        ZoneClassConfig(ZoneClass.RESIDENTIAL, 0.55, 5_000.0),
        ZoneClassConfig(ZoneClass.MIXED, 0.20, 3_000.0),
        ZoneClassConfig(ZoneClass.PUBLIC, 0.15, 2_500.0),
        ZoneClassConfig(ZoneClass.RECREATION, 0.10, 4_000.0),
    ),
    adjacency_rules=(
        ZoneAdjacencyRule(
            ZoneClass.RESIDENTIAL,
            ZoneClass.RECREATION,
            ZoneAdjacencyPolicy.PREFERRED,
        ),
    ),
)
```

## Scope boundary

S05-T01 intentionally does not implement:

- loading existing fixed zones into `TerritorySnapshot` — S05-T02;
- deterministic seed placement — S05-T03;
- partition geometry — S05-T04;
- zone assignment or growth — S05-T05/S05-T06;
- constraint-engine evaluation — S05-T07;
- persistence — S05-T08;
- UI — S05-T09.
