# S12-T12 — Canonical provenance manifest

> **Status: Implemented in UG-AI-077**

## Owner and purpose

`SqlAlchemyProvenanceManifestService.build(run_id=...)` is a read-only projection
of a **successful** `GenerationRun`. Its `ProvenanceManifest.content` is a single
UTF-8 JSON document and `checksum` is `sha256:<64 lowercase hex>` over those exact
bytes. `as_dict()` returns a separate decoded object. The generator does not
execute the GIS pipeline, modify successful runs, upload artifacts, or add a
second Stage/Artifact/metric identity model.

It reads all constituent DB records from a PostgreSQL `REPEATABLE READ, READ ONLY`
transaction. Repeated reads of the same immutable provenance produce byte-for-byte
identical JSON and checksum. The output deliberately excludes generation time,
database insertion order, arbitrary source metadata, local storage paths, and
untrusted filenames.

## Schema v1

A single canonical JSON object has these members:

- `schema_version: "1"`;
- `run`: `id`, `project_id`, nullable `rerun_source_id`, `mode`, `seed`,
  metric `working_srid` and terminal `status`;
- `code`: the recorded 40-character lowercase `commit_sha`;
- `config`: `schema_version`, `checksum` over canonical full config JSON and
  its JSON object `value`; the config encoding matches
  `ScenarioConfigVariant` / S12-T11;
- `datasets`: sorted by kind/dataset ID/version/version ID, with pinned dataset
  and version IDs, version number, `sha256:` source checksum and one
  `source_artifact` (stable artifact ID, logical URI, checksum, size, MIME type);
- `stages`: sorted by canonical stage name; each entry has name/version/status,
  separate `input_hash`, `config_hash`, nullable `output_fingerprint`, and
  published relationally linked artifacts sorted by logical URI and ID;
- `evaluation`: persisted S11 evaluation version, score-config/profile
  identity/version, composite score and canonical raw score inputs as
  `metric_id`, registered raw `unit`, registry definition version, nullable
  `raw_value`. Raw metrics follow `CANONICAL_METRIC_REGISTRY.metric_ids` order,
  not arbitrary persisted array order.

Artifact metadata is taken only from `artifacts` and
`run_stage_result_artifacts`, and always retains the authoritative owner pair.
`RunStageResult.artifact_refs_json` is legacy metadata, not a substitute for the
published relational link. Stage output fingerprint is **not** reconstructed from
artifact checksum, and input/config hashes are never conflated. A skipped stage
has null output fingerprint and no published artifacts.

JSON bytes use `json.dumps(sort_keys=True, separators=(",", ":"),
ensure_ascii=False, allow_nan=False)` followed by UTF-8 encoding. SHA-256
is computed on this complete document, excluding its checksum; the checksum
is returned in the value object rather than recursively embedded in JSON.

## Failure and scope

The service fails closed if the run is absent/non-successful, lacks a valid
commit or canonical finite configuration, includes a foreign/unready dataset,
has an invalid dataset checksum or mismatched source-artifact ownership, has
missing/nonterminal stage results or missing successful output fingerprint,
contains an unreferenced/misowned stage artifact, or has no valid persisted S11
evaluation envelope. It makes no partial writes on error.

This is **metadata provenance**, not a new physical integrity scan: checksums
and publication metadata are read from PostgreSQL. `SqlAlchemyExactRerunService`
continues to validate source blob availability/content and executable code
revision before a rerun. The v1 manifest includes raw inputs persisted in the
canonical S11 evaluation envelope; it does not fabricate unpersisted
distribution/diagnostic metrics. S13-T10 now exposes these exact canonical bytes through the project-scoped synchronous JSON
transport documented in `docs/PROVENANCE_EXPORT.md`. It deliberately does not create a second
manifest format or a durable export Artifact.

## Acceptance

`tests/integration/test_provenance_manifest.py` checks stable content/digest,
Unicode config canonicalization, stage ordering, linked source/stage artifacts,
registered metric units/IDs, successful-run immutability, missing evaluation,
missing output fingerprint, incorrect ownership/checksum, non-successful and
unknown runs.
