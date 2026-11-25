# P0 / issue #202 — canonical persisted Stage configuration schema

**Scope: typed configuration contract; production admission requires complete v5.**

`backend.app.adapters.stage_config_resolver.CanonicalStageConfigResolver`
implements the existing
`PersistedStageConfigResolver` port used by
`SqlAlchemyPipelineContextAdapter`. It decodes an immutable
`GenerationRun.config_json` into the already defined **core StageConfig
dataclasses**, each bound to the *same* `ConfigRef` derived from the
persisted run. It does not create another Stage model, registry or job type.

The deployed generation worker and new run submissions require
`canonical-stage-configs-v5`, and the embedded schema-version field must
match it. Older resolver versions remain available for offline tests. There is **no implicit migration** of old JSON/legacy configs,
no defaulting an unknown version to the latest schema, and no silent
coercion of strings, booleans, nonfinite numbers or omitted required
fields.

## Format and supported stages

```json
{
  "schema_version": "canonical-stage-configs-v1",
  "stages": {
    "evaluate_constraints": {
      "stage_version": "1.0.0",
      "config": {
        "exclusion_policy": "ANY_TOUCH",
        "max_shapes": 100000,
        "max_cells": 25000000
      }
    }
  }
}
```

This **partial example** is valid *only* for a resolver explicitly configured
with the `evaluate_constraints` stage in an isolated test. A real run must
provide exactly the full selected typed Stage set. The default resolver
requires the original **seven concrete stages** of v1:
`evaluate_constraints`, `suitability`, `zoning`, `roads`,
`blocks_and_parcels`, `buildings`, `demography`. Stage names and versions
are taken directly from their core implementations, not from user-defined
strings; JSON supplies no Python import or constructor path.

The full canonical DAG has twelve stages. Versions v2–v5 extend the
original v1 catalog; v5 includes prepare_snapshot, infrastructure,
final_validation, metrics and persist_manifest. The worker requires v5.

Each `config` object must be a closed, fully materialized tree compatible
with the exact existing core dataclass. Optional defaults defined by core
remain valid; required objects and enum values must be explicit. Nested
unknown keys, unsupported stage versions, unsupported configs, duplicate
JSON object keys, nonfinite numbers, oversized payloads and arrays all
fail closed. Config sizes are bounded to 1 MiB, array lengths to 2048,
with a 24-level recursion cap. Stage bindings are emitted in canonical
DAG order regardless of JSON key order.

## Offline validation

```sh
uv run python -m scripts.validate_generation_config --schema \
  > /private/canonical-generation-config.schema.json
uv run python -m scripts.validate_generation_config \
  --manifest /private/canonical-stage-configs-v1.json
uv run pytest tests/unit/test_persisted_stage_config_resolver.py
```

The schema uses JSON Schema draft 2020-12, forbids extra object properties,
requires the stage version/config pairs and documents nested dataclass and
enum shapes. The CLI does **not** connect to PostGIS, Redis or any
ArtifactStore, and does not queue a job. A successful CLI validation proves
config representation only, not GIS input validity or execution.

## Remaining P0 / #202 blockers

1. Complete the user-facing configuration workflow and multi-layer selection.
2. Verify actual executable code identity and ready source compatibility.
3. Run unseeded project creation → boundary → upload → DatasetVersion ready
   → API run → actual worker → persisted GIS/metrics in both modes.
4. Verify release acceptance and CI on the exact merge/release commit.

Until these are complete, a green unit test and JSON schema must not be
reported as a successful live production generation.


## Schema v2: prepared territory

`canonical-stage-configs-v1` remains unchanged. The explicitly selected
`canonical-stage-configs-v2` adds the eighth concrete stage:

```json
"prepare_snapshot": {
  "stage_version": "1.0.0",
  "config": {"cell_size_m": 10.0, "max_cells": 25000000}
}
```

The complete v2 envelope requires this stage and the original seven.
Use `--schema-version canonical-stage-configs-v2` with the offline CLI.
This is still not a complete 12-stage production configuration.
The early-stage input resolver and slope factor use actual PostGIS and
GeoTIFF sources but are not installed as a partial deployed worker.


The v2 suitability stage additionally accepts optional `landuse_weights`
(the existing LanduseClassWeights contract: version, classes, unknown_policy,
default_score). Positive landuse weight requires the explicit mapping;
v1 remains unchanged. The spatial registry resolves landuse, DEM slope
and road proximity against the run's actual selected sources.

## Schema v3: infrastructure

`canonical-stage-configs-v3` adds the ninth typed stage, infrastructure.
It requires explicit facility types and network snap policy. Existing
facility mappings, candidate accessibility and greedy placement limits are
configurable; the earlier v1/v2 contracts are unchanged. This is not yet a
complete twelve-stage runtime. Offline CLI supports v3.

## Schema v4: final evaluation

`canonical-stage-configs-v4` adds final_validation and metrics, giving eleven
concrete configured stages. Final validation requires the canonical four
aggregate hard bounds, optional soft preferences and a bounded report.
Metrics requires a versioned normalization profile and matching score weights.
Only real scalar metric outputs are consumed; missing values follow the
existing explicit normalization policy. This does not yet install a worker
or complete persist_manifest. Earlier schema versions remain unchanged.

## Schema v5: complete generation

`canonical-stage-configs-v5` configures all twelve stages, including
persist_manifest (schema_version "1"). The deployed worker requires this
complete schema; v1-v4 remain offline/integration contracts. The final stage
publishes provenance only for a valid report; failed hard bounds are preserved
for diagnosis and block generation success.

New run creation and creation of a new manual retry use the same v5 resolver
as the worker before job admission. Invalid configs return HTTP 422 without
creating run/job/outbox records. Historical runs remain readable and existing
retry children retain their idempotent identity. Configuration admission alone
does not prove input completeness or GIS success.


## Initial neighbourhood profile (preview)

`POST /api/v1/generation/planning-config` accepts the bounded
`PlanningParameters` object (all fields optional with explicit defaults;
unknown keys, nonfinite values, noninteger counts and zone shares not summing
to 100 are rejected). It returns the full canonical v5 JSON without database
writes, job creation or execution. The same strict resolver used by admission
and the worker validates the returned config. The browser exposes these
parameters, then submits the resulting editable JSON through normal run creation.

Defaults are **model assumptions, not validated planning standards**:
10 m cell, 60/20/10/10 percent residential/mixed/public/recreation, 16 zoning
seeds, up to 24 road anchors, 20,000 m² max block area, 4 floors, 0.25 coverage
and FAR 1.0, 1,000 target residents, 30 m²/person, 800 m clinic network distance,
1,000 clinic capacity. Floors and coverage consistently set the fixed floor
range, planning GFA multiplier and target FAR. Targets are not guaranteed outputs.

The default suitability source is slope (requires one usable ready DEM,
inverted linear normalization 0–30 degrees); alternatively landuse requires
ready categorical data and recognizes residential, grass, meadow and farmland,
invalidating unknown classes. The profile uses rectangular point buildings in
residential/mixed/public zones, no recreation buildings, and one clinic type.
Existing facility mappings cover clinic only. Advanced users can edit the full
JSON or import a saved config. Code identity verification, multi-factor/user
mapping controls, more archetypes and facilities, source compatibility and
unseeded successful execution of this profile remain acceptance work.
