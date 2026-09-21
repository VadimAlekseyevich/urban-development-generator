# Validation detail contract

> **Status: Implemented through UG-AI-049 / S11-T03**

S11 extends the existing canonical `ConstraintResult` / `ValidationReport` family. It does not
introduce a separate violation model.

## Stable affected-entity reference

`ConstraintEntityRef` carries one stable domain `entity_id`. The identifier is deliberately
opaque to the constraint engine: building, road, block, infrastructure and other producers keep
their own stable ID vocabulary, while `ConstraintResult.scope` continues to identify the
constraint domain.

The reference is optional so aggregate/project-level constraints and existing S04 rules remain
source-compatible.

## Problem geometry

`ConstraintProblemGeometry` carries:

- a Shapely geometry;
- the explicit metric `working_srid`.

The geometry must be non-empty and the SRID must satisfy the canonical projected/metre
`WorkingCRS` contract. Geometry validity is **not** required: an invalid source/generated shape
may itself be the evidence for a geometry-validation violation.

The payload is optional. Non-spatial violations remain valid `ConstraintResult` values.

## ConstraintResult compatibility

`ConstraintResult` keeps the existing fields and hard/soft semantics:

- `code`;
- `severity`;
- `scope`;
- `passed`;
- `message`;
- optional `entity_ref`;
- optional `problem_geometry`;
- optional versioned `soft_penalty` metadata for SOFT rules.

Only failed HARD results block generation. Failed SOFT results remain visible through
`ValidationReport.soft_violations` but do not make the report invalid. Adding detail does not
change `failed`, `blocks_generation`, `hard_failures`, `soft_violations` or `is_valid`.

Existing rules can continue constructing `ConstraintResult` without detail. Rules that can name
the affected entity or spatial evidence can opt into the new fields without changing the
`Constraint` protocol.

`SoftPenaltyMetadata` is valid only on SOFT results. Its v1 contract contains a bounded
`raw_penalty` in `0..1`, a non-negative `weight`, and derived `weighted_penalty`.
A passing SOFT result with metadata must carry zero raw penalty; a failed SOFT result with
metadata must carry a positive raw penalty. Existing SOFT results without metadata remain valid
for backward compatibility. HARD results can never carry soft-penalty metadata.

## Cross-stage aggregation

`aggregate_validation_reports()` combines an immutable tuple of canonical reports in the exact
caller-supplied stage order. It concatenates existing `ConstraintResult` values without copying,
sorting or deduplicating them. This matters because the same constraint code may legitimately be
registered at more than one stage.

The aggregate remains an ordinary `ValidationReport`; hard/soft semantics are therefore derived
by the existing `hard_failures`, `soft_violations` and `is_valid` properties.

Stage identity is orchestration provenance and is intentionally not added to `ConstraintResult`.
The caller that aggregates reports owns the canonical stage order.

## Deterministic serialization

The canonical writer currently emits `VALIDATION_REPORT_SCHEMA_VERSION = 2`.
The reader accepts both v1 and v2: v1 results restore with `soft_penalty=None`, while v2 carries
the optional versioned soft-penalty payload.

`serialize_validation_report()` emits deterministic UTF-8 JSON bytes with sorted object keys and
compact separators. Only source fields are serialized; derived properties such as `is_valid` are
recomputed after reading.

Problem geometry is encoded as deterministic little-endian WKB hex plus explicit
`working_srid`. This is a core persistence/interchange representation, not GeoJSON and not an
HTTP response schema. `deserialize_validation_report()` is strict about schema version, object
shape and the existing domain invariants, and restores the same canonical
`ConstraintResult/ValidationReport` types.

## Ownership boundary

Core owns the typed validation detail, lossless aggregation and canonical versioned codec. Core
does not emit HTTP schemas, GeoJSON or ORM rows.

UG-AI-046..049 keep detail, aggregation, hard aggregate bounds and soft penalties inside the one
canonical validation family. Metric registry metadata is next in **UG-AI-050 / S11-T04**; the
later violations layer API/UI remains S11-T13.
