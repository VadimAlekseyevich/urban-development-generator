# InfrastructureType schema

S10-T01 defines the immutable facility-type contract consumed by later infrastructure
demand, candidate, accessibility and placement stages.

## Required semantics

Every InfrastructureType defines:

- stable version and code;
- one of the minimum v1 categories: education, healthcare, retail, recreation;
- a demand model linked directly to an S09 DemographicDemandCategory;
- positive facility capacity;
- maximum network service distance in metres;
- allowed functional zones;
- minimum and target site area;
- canonical candidate-source policy over blocks, parcels and/or buildings.

Age-group demand additionally names the demographic group to consume. Non-age signals
(population, workforce, jobs) cannot carry a demographic group.

## Determinism and provenance

Allowed zones and candidate sources are canonicalized, so equivalent configurations have
the same equality semantics and SHA-256 fingerprint regardless of input order. All numeric
constraints reject zero, negative, NaN and infinity; target site area cannot be below the
minimum site area.

## Scope boundary

T01 is schema only. It does not supply category presets, map existing facilities, compute
unmet demand, generate candidate sites, snap to the road graph, calculate accessibility or
place facilities. Those responsibilities begin at S10-T02 and continue through the sprint.
