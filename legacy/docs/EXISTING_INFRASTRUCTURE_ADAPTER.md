# Existing infrastructure adapter

S10-T02 converts normalized fixed-facility source records into typed infrastructure input
bound to one immutable TerritorySnapshot.

## Input and provenance

Each source record contains:

- the FACILITIES `source_ref` present in the snapshot;
- stable source feature id and external facility use;
- geometry in the snapshot working CRS;
- optional source capacity and name.

Records from another source ref or CRS are rejected.

## Use and capacity mapping

`ExistingFacilityMappingRule` maps an external facility use to one
`InfrastructureType.code`. Effective capacity follows one explicit precedence:

1. source capacity, when provided;
2. mapping-rule default capacity;
3. InfrastructureType capacity.

Unknown uses may be skipped for tolerant ingest or rejected in strict mode. Duplicate
mapping uses, duplicate infrastructure type codes and duplicate source feature identities
are rejected.

## Output

The immutable result contains sorted fixed facilities with source provenance, mapped
infrastructure type, effective capacity, geometry and working SRID plus deterministic
diagnostics.

The geometry is intentionally preserved because S10-T06 will snap existing facilities and
candidate sites to the road-network snapshot.

## Scope boundary

T02 is a pure adapter. It does not query persistence, compute unmet demand, perform
accessibility routing or place generated facilities.
