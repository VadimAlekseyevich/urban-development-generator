# Infrastructure candidate-site generator

S10-T04 turns existing spatial subjects into deterministic bounded facility candidates.

## Candidate sources

The generator can consume:

- zone-associated blocks;
- planning parcels;
- generated buildings.

Only source kinds enabled by `InfrastructureType.candidate_policy.sources` participate.
Candidates whose zone class is not listed in `InfrastructureType.allowed_zones` are
filtered. Unzoned blocks/parcels are skipped explicitly.

## Candidate payload

Each candidate stores:

- infrastructure type code;
- source kind and source id;
- zone class and optional zone/block ids;
- working SRID;
- source geometry;
- available source area;
- a representative-point anchor guaranteed to lie inside the source geometry.

Planning-parcel candidates use the parcel buildable envelope as their available site.

## Determinism and bounds

Inputs are sorted by stable ids, candidate ids are canonical
`{type}:{source_kind}:{source_id}`, and duplicate source ids are rejected per source kind.
The generator enforces separate total-input and candidate-count limits.

## Scope boundary

T04 does not manufacture a final facility site/footprint. S10-T05 creates candidate
geometry, while S10-T09 applies capacity/site feasibility. Therefore minimum/target site
area are preserved as later constraints rather than used as premature T04 filters.
