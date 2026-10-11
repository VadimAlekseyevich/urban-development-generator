# Delivery risk review — engineering audit, 2026-10-11

**Scope:** source, architecture documents, CI configuration and recent
exact-head successful workflows through UG-AI-094, plus S14 queue changes.
This is a **static review plus evidence from CI**, not a production load test,
penetration test or real-territory end-to-end demonstration.

## Verdict

**Architecture direction: viable; integrated release readiness: NOT YET.**
The risk is less a sudden collapse of the Stage/CRS/ownership architecture
than a false final milestone due to green component tests, synthetically
seeded browser data and unclosed deploy/runtime functionality.

### P0 — Real generation worker has no production runtime composition

- **Evidence:** `worker/tasks.py:run_generation` requires a
  `generation_runtime_factory` in ARQ task context unless tests supply a
  `generation_job_service` override. `worker/settings.py` has no startup
  hook installing that factory, including the new queue-specific settings.
- **Effect:** A real `POST /projects/{id}/runs` may create a valid DB/outbox
  record but `worker-generation` cannot execute the Stage DAG and raises
  `GenerationTaskConfigurationError`. The S12 worker tests explicitly
  inject a tiny synthetic Stage runtime; that proves the contract but **not**
  production worker composition.
- **Required closure:** implement executable runtime assembly from canonical
  stages, input readers and config, wire ARQ startup, and test with the actual
  worker process and non-synthetic input through persisted evaluation.

### P0 — Browser upload could not produce a ready version (remediation in PR #180)

- **Evidence:** `POST /uploads` only stores an Artifact; the S13 E2E
  documentation and `scripts/seed_browser_e2e.py` explicitly insert a
  separate ready DatasetVersion and successful evaluations into PostgreSQL.
- **Effect:** A user can upload and create a project but cannot complete
  the proven browser workflow without an out-of-band ingest job setup.
- **Remediation evidence:** PR #180 implements project-scoped Artifact→DatasetVersion\n  scheduling over persisted Job/JobOutbox and adds an unseeded Playwright\n  ingest-through-worker fixture. Verify that the browser and worker CI jobs\n  are green on the **exact merged commit** before changing the P0 disposition.\n  This resolves ingestion only; real production generation remains blocked.

### P1 — No enforced user identity / project authorization at HTTP edge

- **Evidence:** `backend/app/main.py` installs request context and CORS
  middleware but no authentication/authorization enforcement for project
  APIs. Project UUID validation/owner joins verify referential ownership
  **within** the data model, not ownership by a human account.
- **Effect:** if API is exposed beyond a trusted development network,
  possession of an endpoint suffices for operations including creation and
  uploads; UUIDs are not credentials.
- **Required closure:** define deployment trust boundary and enforce
  authentication/authorization before a multi-user or internet release.
  The dedicated security audit is UG-AI-105 / S14-T11.

### P1 — Operational envelope not evidenced

- **Evidence:** CI has unit/integration tests, synthetic benchmarks and
  Compose startup smoke, but no full-scale 25/100 km² peak-memory acceptance,
  queue overload testing, crash-recovery soak test, backup/restore exercise
  or production security gate. S14 tasks 096–109 explicitly schedule these.
- **Effect:** memory pressure, starvation, pathological geometry and retry
  behavior can surface late even when all current CI jobs are green.
- **Required closure:** S14 backpressure, profiling, operations telemetry,
  backup/migrations, fault injection and full release acceptance.

### P2 — Planning/proof language can overstate user-ready capability

- **Evidence:** `docs/07-planning/MILESTONES.md` labels M3/M4 complete
  **only as contract/integration gates**, while its M5 browser item and the
  sprint progress might sound like the raw upload was actually ingested or
  the worker computed the E2E successful runs. `docs/PLAYWRIGHT_E2E.md`
  correctly states the fixture limitation.
- **Effect:** downstream users may misinterpret a green test and deploy an
  incomplete workflow. Keep acceptance labels specific to what was executed.
- **Required closure:** make product-readiness claims conditional on a real
  unseeded, domain-sized run. Keep this evidence ledger current.

## Evidence in favor of the project (not just optimism)

1. The accepted **M0 architecture regression matrix** enforces core
   independence from HTTP/SQL/worker code, a unique typed Stage/StageResult,
   unique raw metric vocabulary, source/generated ownership, CRS contracts
   and deterministic stage provenance.
2. Database migrations, GeoJSON/MVT query limits, normalized score and
   validation serialization, immutable successful runs and PostGIS joins
   have regression tests in the mandatory Python CI.
3. The outbox dispatcher uses `FOR UPDATE SKIP LOCKED`, deterministic ARQ
   `job:<uuid>` keys and retryable persisted delivery, with tests covering
   retries and PostgreSQL recovery. S14 isolates heavy task types.
4. S13 Playwright actually exercised **Chromium→React→FastAPI→PostGIS** for
   a source feature, binary multipart upload and persisted CSV comparison.
   This is valuable UI/backend integration evidence while *not* validating
   a production run through ingest and generation.
5. The docs explicitly separate milestones, sprint completion and v1.0
   acceptance. Reuse this distinction in release communications.

## Release gates that must not be waived

- **Gate A:** a real uploaded source Artifact yields a ready DatasetVersion
  through an auditable Job/outbox, with immutable source rows and provenance.
- **Gate B:** the deployed worker actually constructs the canonical runtime
  and completes a successful GenerationRun with persisted stages, metrics,
  validation and GIS outputs **without fixture injection**.
- **Gate C:** expand/from-scratch and repeatability tested on representative
  territories, both small and stress-sized; validate CRS, memory, score
  consistency and failure recovery.
- **Gate D:** authentication/security boundary, explicit resource limits,
  backup/restore and graceful queue rollout/rollback are demonstrated.
- **Gate E:** green CI on **exact merged commit** including a real end-to-end
  release journey; synthetic tests remain useful but are not the sole proof.

**Status:** do not describe the project as production-ready until A–E pass.
This review does not assert the project will collapse; it identifies concrete
release blockers and existing tests that prevent silent architectural drift.
