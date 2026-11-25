# P0 / issue #202 — canonical persisted Stage configuration schema

**Scope: first configuration-only boundary, not production generation readiness.**

`backend.app.adapters.stage_config_resolver.CanonicalStageConfigResolver`
implements the existing
`PersistedStageConfigResolver` port used by
`SqlAlchemyPipelineContextAdapter`. It decodes an immutable
`GenerationRun.config_json` into the already defined **core StageConfig
dataclasses**, each bound to the *same* `ConfigRef` derived from the
persisted run. It does not create another Stage model, registry or job type.

`GenerationRun.config_schema_version` must equal
`canonical-stage-configs-v1`, and the embedded schema-version field must
match it. There is **no implicit migration** of old JSON/legacy configs,
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
requires all **seven currently implemented concrete stages**:
`evaluate_constraints`, `suitability`, `zoning`, `roads`,
`blocks_and_parcels`, `buildings`, `demography`. Stage names and versions
are taken directly from their core implementations, not from user-defined
strings; JSON supplies no Python import or constructor path.

The full stable canonical DAG has **twelve** identities. The remaining
`prepare_snapshot`, `infrastructure`, `final_validation`, `metrics`
and `persist_manifest` currently have **no corresponding complete typed
Stage implementation/config binding**. This module deliberately rejects
those entries instead of inventing no-op stages or falsely reporting a
fully wired generation runtime. Do not turn the worker on merely because
a valid seven-stage config has been decoded.

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

1. Implement the missing canonical stage implementations with complete
   real typed inputs, fixed-input semantics, generated output publication,
   validation, metrics and manifest persistence.
2. Compose the production StageRegistry/input resolver against the
   existing PostGIS/ArtifactStore ports, and install a
   `generation_runtime_factory` in the ARQ generation-worker lifecycle.
3. Apply the schema to the generation submission API with backward
   compatibility explicitly designed; old config payloads must not be
   silently reinterpreted.
4. Run **unseeded** project creation → geometry boundary → upload →
   DatasetVersion `ready` → normal GenerationRun → actual worker →
   persisted generated GIS/metrics with two modes and real datasets.
5. Confirm release acceptance and CI on the exact merge/release commit.
   The boundary-input PR #201 is related but separate.

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
