# Constraint registry and engine

S04-T01 introduces the concrete, infrastructure-independent constraint registry used by
algorithm stages.

## Public boundary

The base rule contract remains `core.urban_generator.domain.Constraint`. A rule owns stable
metadata:

- `code` — stable diagnostic identity;
- `severity` — `HARD` or `SOFT`;
- `scope` — one canonical `ConstraintScope`;
- `evaluate(...)` — pure domain evaluation against a subject, immutable
  `TerritorySnapshot`, and `RunContext`.

`ConstraintEngine.evaluate(...)` now also requires an explicit `stage` name. This makes the
evaluation key an exact `(stage, scope)` pair instead of implicitly running every rule that
happens to share a scope.

Concrete composition lives in `core.urban_generator.constraints`:

```python
registry = ConstraintRegistry()
registry.register(stage="roads", constraint=max_slope_rule)
registry.register(stage="buildings", constraint=setback_rule)

engine = RegisteredConstraintEngine(registry)
report = engine.evaluate(
    subject=candidate,
    stage="roads",
    scope=ConstraintScope.ROAD,
    snapshot=snapshot,
    context=context,
)
```

The core package does not import FastAPI, SQLAlchemy, Redis, ARQ, or React.

## Registration semantics

A registration is identified by `(stage, scope, code)`.

- Stage names use the same lowercase/underscore shape as algorithm stage names:
  `^[a-z][a-z0-9_]{0,63}$`.
- Rule `code`, `severity`, and `scope` are validated with the existing domain contract.
- Registering the same `(stage, scope, code)` twice is rejected.
- The same stable rule code may be bound to another stage intentionally.
- `rules_for(stage=..., scope=...)` is exact: rules from another stage or another scope never
  leak into the evaluation.
- Rules are returned/evaluated in stable lexical `code` order, independent of registration
  order.

Registry composition is expected during application/bootstrap setup, before a generation run
starts. Run inputs and rule configuration remain the responsibility of immutable run/config
references.

## Evaluation semantics

`RegisteredConstraintEngine` is the single evaluation API for this foundation.

For each selected rule it calls `evaluate(...)` and requires a real `ConstraintResult`. The
engine additionally verifies that the result still has the rule's registered `code`,
`severity`, and `scope`. A rule therefore cannot silently downgrade a HARD failure, switch
scope, or report under another code.

The returned `ValidationReport` keeps the existing semantics:

- failed `HARD` result -> report is invalid and blocks generation;
- failed `SOFT` result -> visible violation, but the report remains valid if there are no HARD
  failures;
- no rules for a stage/scope -> valid empty report.

Rule exceptions are not swallowed by the engine. Error taxonomy/retry handling belongs to the
calling stage/application boundary.

## Deliberately out of scope

S04-T01 does **not** implement GIS rules. The roadmap keeps them separate:

- S04-T02 — geometry exclusion;
- S04-T03 — distance/setback;
- S04-T04 — raster threshold.

Those rules should register through this engine rather than add stage-specific ad-hoc
validation loops.


## S11 aggregate final-validation bounds

UG-AI-048 keeps aggregate validation inside the same registry/engine contract. The canonical
aggregate subject carries authoritative scalar values keyed by existing `RawMetricId` values;
it does not introduce another metric identity vocabulary.

The S11-T02 registration builder requires exactly these four aggregate metrics:

- `buildings.coverage_ratio`;
- `buildings.far`;
- `demography.density_per_km2`;
- `infrastructure.capacity_utilization`.

Each configured `AggregateMetricBound` is inclusive and may define a lower bound, an upper
bound, or both. The four rules are HARD `TERRITORY` constraints registered only at the
canonical `final_validation` stage. Registration order is normalized by metric ID, while the
shared engine still evaluates by stable constraint code order.

A finite value outside its configured range becomes a normal failed `ConstraintResult`.
Malformed configuration, duplicate/missing subject metrics, non-finite values, or inconsistent
snapshot/run CRS are input-contract errors rather than synthetic violations. S11-T03 owns soft
penalty semantics; these S11-T02 rules do not overload HARD invalidity with penalty scoring.
