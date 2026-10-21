# S15-T01 — Experiment manifest over canonical ScenarioBatch

**UG-AI-110 — draft**. A research experiment has a **versioned JSON manifest**
with dimensions \`territory × config × ablation × seed\`. It does **not**
have a separate Stage, Result, Job, Redis task, run-status model, or database
table. Only existing \`SqlAlchemyScenarioBatchStore.create_matrix\` inserts
generation runs, dataset-version bindings, jobs, outbox and memberships.

## Manifest v1

The JSON Schema is exposed by \`python -m scripts.experiment_matrix --schema\`.
Unknown keys are rejected. Example with one territory (a real project UUID
and an existing ready project-owned DatasetVersion must be substituted):

\`\`\`json
{
  "schema_version": "s15-experiment-matrix-v1",
  "experiment_id": "density-v1",
  "territories": [
    {
      "name": "territory-a",
      "project_id": "00000000-0000-0000-0000-000000000001",
      "mode": "EXPANSION",
      "dataset_version_ids": ["00000000-0000-0000-0000-000000000002"]
    }
  ],
  "seeds": [7, 11, 19],
  "cells": [
    {
      "config_name": "baseline",
      "ablation_name": "full",
      "config_json": {"density": 1}
    },
    {
      "config_name": "baseline",
      "ablation_name": "no_soft",
      "config_json": {"density": 1, "soft_constraints_enabled": false}
    }
  ],
  "config_schema_version": "example-v1",
  "commit_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "concurrency_limit": 2
}
\`\`\`

These configs are **illustrative JSON snapshots only**, not a claim that
the deployed production Stage catalog accepts those keys. Input config must
be a **full, versioned executable config**, never an implicit JSON patch.
The ablation label is research metadata: disabling a constraint must occur
through the actual canonical config/stage implementation and should be
verified independently. The lowercase \`config_name\` and \`ablation_name\`
form the canonical S12 variant label \`config__ablation\`.

This example creates six normal runs in one sealed batch. Each territory
gets one batch of **3–10 children**; at most four distinct project-owned
territories per manifest. The full config/ablation grid must be rectangular,
and each variant config must be different. Seeds are explicit, unique,
nonnegative and signed BIGINT-safe. Validated dataset-version IDs, mode,
commit SHA, schema version, SRID, project boundary, per-project admission
and batch concurrency all remain owned by S12.

## Preview and submission

\`\`\`sh
uv run python -m scripts.experiment_matrix --schema
uv run python -m scripts.experiment_matrix --manifest /private/study.json
# Requires migrated PostGIS + dispatcher, and existing ready dataset versions:
uv run python -m scripts.experiment_matrix --manifest /private/study.json \
  --territory territory-a --execute --receipt /private/study-territory-a.receipt.json
\`\`\`

Preview parses the manifest, expands exact per-territory child rows in
**territory name**, **variant label** and **ascending seed** order, and
prints a deterministic SHA-256 digest of the full normalized plan (including
project/version/code/config/ablation provenance); it makes **no DB call**.
Permutation of territory, cell, seed or dataset-version ordering produces
the same digest. This digest identifies the **plan**, not successful GIS
results. The generated plan does not contain an invented run UUID.

Execution is explicitly requested for **one territory at a time** and calls
the existing S12 \`create_matrix\` transaction once. It returns the genuine
\`ScenarioBatch.id\` and ordered \`GenerationRun.id\` references. The worker
and outbox dispatcher process them exactly as they process UI-created runs.
A different territory is a separate transaction and can fail independently;
there is no cross-territory atomic commit.

The receipt path must be new. Before the DB call the CLI reserves it with
\`status=reserved\`; after a confirmed commit it writes \`submitted\` with
canonical batch and run IDs. A crash or write error **after DB commit**
can leave an incomplete receipt. Do not blindly repeat the command or
delete the reserved file: inspect the project’s \`scenario_batches\`,
\`scenario_batch_runs\`, \`generation_runs\`, \`jobs\` and \`job_outbox\` and
reconcile first. Repeating a manifest intentionally creates **new** run
UUIDs (S12 semantics); the plan hash is **not** an idempotency key.
Do not submit untrusted manifests against a production API account.

## Acceptance and limits

\`\`\`sh
uv run pytest tests/unit/test_experiment_matrix.py
uv run pytest tests/integration/test_experiment_matrix.py
uv run ruff check .
uv run mypy backend core worker
\`\`\`

Unit tests cover JSON schema version and unknown fields, full factorial
coverage, canonical permutation hash, bad seeds/NaN/duplicate inputs, and
non-mutating CLI preview. PostgreSQL tests cover two independent territory
batches, canonical Job/Outbox creation, ordinary claim/capacity and rollback
on invalid project-owned versions. All ordinary CI release gates still apply.

This is the **schema/runner wiring only**: no real Territory A/B data
packages, observed morphology/reproducibility metrics, ablation validity
evidence, experiment CSV exporter, production runtime fix or R1–R9 claim.
Those remain UG-AI-111 onward and the separately tracked release blockers.
