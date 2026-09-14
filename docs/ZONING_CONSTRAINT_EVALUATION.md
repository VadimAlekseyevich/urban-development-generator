# Zoning constraint evaluation

S05-T07 connects generated functional zoning to the shared core `ConstraintEngine` contract.

## Contract

`ZoneConstraintEvaluator` receives:

- a `ZoningPartitionResult`;
- the current `ZoneAssignmentResult` (including refined labels from S05-T06);
- the immutable `TerritorySnapshot`;
- the `RunContext`;
- any implementation of the shared `ConstraintEngine` protocol.

For every generated partition cell, the evaluator creates a `ZoneConstraintSubject` and calls:

```python
engine.evaluate(
    subject=subject,
    stage="zoning",
    scope=ConstraintScope.ZONE,
    snapshot=snapshot,
    context=context,
)
```

The returned `ValidationReport` is preserved per zone and is also available as one aggregate report through `ZoneConstraintEvaluationResult.report`.

## No duplicate zoning checks

The evaluator intentionally does **not** accept `ZoningConfig` and does not reimplement:

- minimum zone area rules;
- adjacency policy checks;
- suitability rules;
- geometry exclusion/setback rules;
- any other planning rule.

Those rules belong to implementations registered in the shared constraint registry/engine. The evaluator only performs input-contract checks required to safely route the generated zone through that engine: type/alignment checks, valid polygon geometry, and consistent working metric CRS.

This prevents a future rule from having one implementation in zoning code and another implementation in the constraint engine.

## Hard and soft semantics

The evaluator does not reinterpret severities. `ConstraintSeverity.HARD` and `ConstraintSeverity.SOFT` remain defined by the shared domain contract:

- a failed HARD result makes the zone report invalid and blocks generation at the validation-policy layer;
- a failed SOFT result remains visible but does not make the report invalid.

`valid_zone_count` / `invalid_zone_count` therefore follow `ValidationReport.is_valid` exactly.

## CRS

Each `ZoneConstraintSubject` carries `working_srid`. The partition, project snapshot and run context must all use the same project working metric CRS. No area or distance rule should be evaluated in EPSG:4326.

## Scope boundary

S05-T07 does not add persistence, API endpoints or UI. S05-T08 persists generated zones and their run/area/class/diagnostic data. S05-T09 exposes generated vs fixed zoning in the interactive map.
