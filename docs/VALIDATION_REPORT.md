# Validation detail contract

> **Status: Implemented through UG-AI-046 / S11-T01**

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
- optional `problem_geometry`.

Only failed HARD results block generation. Failed SOFT results remain visible through
`ValidationReport.soft_violations` but do not make the report invalid. Adding detail does not
change `failed`, `blocks_generation`, `hard_failures`, `soft_violations` or `is_valid`.

Existing rules can continue constructing `ConstraintResult` without detail. Rules that can name
the affected entity or spatial evidence can opt into the new fields without changing the
`Constraint` protocol.

## Ownership boundary

Core owns the typed validation detail. Core does not emit HTTP schemas, GeoJSON or ORM rows.

Cross-stage aggregation, deterministic serialization and evidence that enriched reports preserve
detail across stage boundaries belong to **UG-AI-047 / S11-T01**. The later violations layer
API/UI is S11-T13.
