# Urban Development Generator — AI Execution Tasks

> **Status: Active**
>
> Ordered one-request-sized execution backlog. Parent capability roadmap remains `docs/IMPLEMENTATION_VERSION_ROADMAP.md`.

## Rules

- Execute one UG-AI task at a time unless a task explicitly names a paired acceptance step.
- Read `DOCUMENTATION_RULES.md`, `IMPLEMENTATION_READINESS.md`, the parent roadmap item, and canonical subsystem docs first.
- Do not implement later UG-AI tasks opportunistically.
- Do not introduce a second Stage/Constraint/NetworkBackend/ArtifactStore/metric vocabulary.
- Architecture change requires ADR before or with code.
- Update tests and canonical docs in the same change.
- Spatial work must state CRS/units and be bounded/indexed.
- Random behavior uses RunContext namespaced RNG.
- Job work must address idempotency/retry/cancellation/artifact cleanup as applicable.
- A task is not DONE with red required CI.

## Completion report required for every task

Report:
- changed files;
- contract created/changed;
- tests/checks run;
- CI status;
- known limitations;
- exact next UG-AI task.

## M0 / Architecture stabilization

Historical `STAB-*` labels below map to the finite M0 stabilization work. M0-01 through M0-08 have now supplied the evidence for UG-AI-001 through UG-AI-012; UG-AI-013/014 remain the CI/readiness closure.

- [x] **UG-AI-001** — **STAB-01** — Delete legacy core/urban_generator/pipeline/service.py and stages.py after confirming no repository imports; do not replace them with another enum.
- [x] **UG-AI-002** — **STAB-02** — Synchronize DEVELOPMENT_PLAN RunContext/StageResult wording with PIPELINE_MODEL and ADR-0001.
- [x] **UG-AI-003** — **STAB-03** — Define canonical coarse stage names, versions, dependency identities and typed adapter input/output ownership for S04-S09.
- [x] **UG-AI-004** — **STAB-04** — Implement suitability/constraint stage adapter(s) over existing constraint and suitability services; no algorithm duplication or persistence.
- [x] **UG-AI-005** — **STAB-05** — Implement zoning stage adapter over existing fixed-zone, seed, partition, assignment, refinement and validation capabilities.
- [x] **UG-AI-006** — **STAB-06** — Implement roads stage adapter over canonical road semantics/graph/generation/validation/metrics services and NetworkBackend boundary.
- [x] **UG-AI-007** — **STAB-07** — Implement blocks_and_parcels stage adapter over polygonize/clip/metrics/frontage/split/cleanup/association/subdivision capabilities.
- [x] **UG-AI-008** — **STAB-08** — Implement buildings stage adapter over envelope/candidate/archetype/orientation/spacing/convergence/attributes/area capabilities.
- [x] **UG-AI-009** — **STAB-09** — Implement demography stage adapter over scenario/capacity/allocation/cohorts/employment/aggregation/calibration/demand/metrics capabilities.
- [x] **UG-AI-010** — **STAB-10** — Add synthetic in-memory typed execution-spine fixture that composes stabilized adapters through demography without FastAPI/SQLAlchemy/Redis.
- [x] **UG-AI-011** — **STAB-11** — Add deterministic fingerprint/permutation tests and stage metadata dependency validation for the execution spine.
- [x] **UG-AI-012** — **STAB-12** — Re-audit S10-S15 task contracts against stabilized adapter outputs and update ROADMAP_AUDIT/roadmap where mismatched.
- [x] **UG-AI-013** — **STAB-13** — Run required Python/frontend/migration/compose CI checks and fix stabilization regressions only.
- [x] **UG-AI-014** — **STAB-14** — Close architecture debt findings, mark IMPLEMENTATION_READINESS Accepted, and record stabilization commit/CI evidence.

## S10 / Infrastructure and accessibility

- [x] **UG-AI-015** — **S10-T06** — Define typed infrastructure network-snap input/output records for demand, existing facilities and candidate sites using stable domain refs.
- [x] **UG-AI-016** — **S10-T06** — Define max snap distance, metric CRS compatibility, unsnapped reason/diagnostic semantics and batch-size bound.
- [x] **UG-AI-017** — **S10-T06** — Implement deterministic bounded batch snapping exclusively through NetworkBackend.snap().
- [x] **UG-AI-018** — **S10-T06** — Add exact-hit/tie/outside-limit/empty-network/mixed-result tests; prove input permutation does not change identity/results.
- [x] **UG-AI-019** — **S10-T06** — Document T06 boundary and explicitly prohibit direct NetworkX/SpatialSnapIndex use from infrastructure.
- [x] **UG-AI-020** — **S10-T07** — Define accessibility query/result contract keyed by infrastructure type, demand ref and facility/site ref.
- [x] **UG-AI-021** — **S10-T07** — Implement bounded existing-facility accessibility using NetworkBackend multi-source distance operations and type max-distance cutoffs.
- [x] **UG-AI-022** — **S10-T07** — Implement candidate-site accessibility in batches without all-pairs materialization beyond configured bounds.
- [x] **UG-AI-023** — **S10-T07** — Define unreachable/unsnapped handling without treating missing paths as zero/infinite numeric values.
- [x] **UG-AI-024** — **S10-T07** — Add deterministic synthetic graph tests and path-search budget/performance assertions.
- [x] **UG-AI-025** — **S10-T08** — Define greedy placement state: remaining demand, accepted facilities, coverage cache and deterministic candidate ordering.
- [x] **UG-AI-026** — **S10-T08** — Implement incremental candidate benefit calculation from T07 results; no pathfinding inside placement loop.
- [x] **UG-AI-027** — **S10-T08** — Implement bounded facility count/iterations and deterministic tie-breaking.
- [x] **UG-AI-028** — **S10-T08** — Update remaining demand after each accepted facility without negative demand or double coverage.
- [x] **UG-AI-029** — **S10-T08** — Add known-optimum/saturation/tie/no-feasible-candidate tests.
- [x] **UG-AI-030** — **S10-T09** — Define capacity/site/host-building feasibility result and rejection reasons.
- [x] **UG-AI-031** — **S10-T09** — Validate proposed capacity against InfrastructureType and explicit site/host geometry without regenerating sites.
- [x] **UG-AI-032** — **S10-T09** — Integrate feasibility filter before greedy acceptance and test impossible/edge capacities.
- [x] **UG-AI-033** — **S10-T10** — Add typed GeneratedInfrastructure persistence schema/migration with run/category/capacity/site-or-host/network refs.
- [x] **UG-AI-034** — **S10-T10** — Implement retry-safe bounded writer preserving successful-run immutability and deterministic generated identity.
- [x] **UG-AI-035** — **S10-T10** — Add persistence/database integration tests and required run/spatial indexes.
- [x] **UG-AI-036** — **S10-T11** — Compute canonical infrastructure RawMetricId values from T07-T10 results without rerunning routing.
- [x] **UG-AI-037** — **S10-T11** — Define percentile/unmet/utilization empty-data and unreachable policies.
- [x] **UG-AI-038** — **S10-T11** — Add numeric/property tests for coverage bounds, percentiles and capacity conservation.
- [x] **UG-AI-039** — **S10-T12** — Add run-scoped infrastructure read service/repository API with bounded bbox access and existing/generated distinction.
- [x] **UG-AI-040** — **S10-T12** — Add frontend infrastructure panel/layers for facilities, candidates/generated sites, unmet demand and selected accessibility detail.
- [x] **UG-AI-041** — **S10-T12** — Keep authoritative computation on backend and add UI error/truncation/run-selection states.
- [x] **UG-AI-042** — **S10-T13** — Build one synthetic-town end-to-end fixture from demography demand through network snap/accessibility/placement/persistence/metrics.
- [x] **UG-AI-043** — **S10-T13** — Assert expected coverage ranges, fixed facility contribution, deterministic output and no fixed-state mutation.
- [x] **UG-AI-044** — **S10-T14** — Add reference candidate×demand performance fixture and counters proving no repeated full all-pairs recomputation.
- [x] **UG-AI-045** — **S10-T14** — Record benchmark envelope/diagnostics and close S10 sprint gate only with green CI.

## S11–v1.0 / Ordered future execution

- [ ] **UG-AI-046** — **S11-T01** — Extend ConstraintResult/ValidationReport with optional stable entity reference and problem-geometry payload while preserving existing hard/soft semantics.
- [ ] **UG-AI-047** — **S11-T01** — Add cross-stage validation aggregation and serialization tests; do not create a competing violation domain model.
- [ ] **UG-AI-048** — **S11-T02** — Implement aggregate coverage/FAR/density/capacity constraints through ConstraintEngine registrations.
- [ ] **UG-AI-049** — **S11-T03** — Define versioned soft-penalty result metadata and implement soft rules independently from hard invalidity.
- [ ] **UG-AI-050** — **S11-T04** — Extend MetricDefinition around existing RawMetricId with scope/direction/source/version metadata and a canonical registry.
- [ ] **UG-AI-051** — **S11-T05** — Implement land/building raw metric adapter from authoritative suitability/block/building results.
- [ ] **UG-AI-052** — **S11-T06** — Implement road raw metric adapter reusing road graph/metrics artifacts without graph rebuild.
- [ ] **UG-AI-053** — **S11-T07** — Implement demography raw metric adapter from persisted/typed demography outputs.
- [ ] **UG-AI-054** — **S11-T08** — Implement infrastructure raw metric adapter reusing S10 metrics/results.
- [ ] **UG-AI-055** — **S11-T09** — Implement constraint raw metrics from canonical ValidationReport results.
- [ ] **UG-AI-056** — **S11-T10** — Define versioned normalization direction/range/clamp/missing policy and tests.
- [ ] **UG-AI-057** — **S11-T11** — Implement composite score as a pure transformation over raw metrics + normalization + weights; persist raw inputs.
- [ ] **UG-AI-058** — **S11-T12** — Implement score sensitivity over persisted raw metrics with no GIS rerun.
- [ ] **UG-AI-059** — **S11-T13** — Deliver run-scoped violations layer API/UI from canonical validation details.
- [ ] **UG-AI-060** — **S11-T14** — Deliver metrics dashboard showing raw value/unit, normalized value, weight and score contribution.
- [ ] **UG-AI-061** — **S11-T15** — Add integrated regression fixtures with expected ranges/invariants and canonical metric IDs.
- [ ] **UG-AI-062** — **S12-T01** — Implement typed Stage registry/DAG validation over stabilized Stage metadata; reject missing deps, cycles and duplicate names.
- [ ] **UG-AI-063** — **S12-T01** — Define deterministic topological order and explicit skip semantics with unit tests.
- [ ] **UG-AI-064** — **S12-T02** — Define persistence-to-core PipelineContext assembly contract for RunContext, TerritorySnapshot, configs and ports.
- [ ] **UG-AI-065** — **S12-T02** — Implement adapter without exposing ORM/session types to core.
- [ ] **UG-AI-066** — **S12-T03** — Define checkpoint eligibility from stage name/version + independent input/config hashes; resolved input identity must include ordered dependency `RunStageResult.output_fingerprint` values, while the current stage output fingerprint remains a separate persisted provenance field.
- [ ] **UG-AI-067** — **S12-T03** — Implement persisted checkpoint eligibility/reuse requiring matching name/version/input/config provenance and non-null dependency/output fingerprints; add stale/missing-fingerprint rejection tests.
- [ ] **UG-AI-068** — **S12-T04** — Replace worker.run_generation placeholder with real DAG execution outside HTTP and persisted stage progress.
- [ ] **UG-AI-069** — **S12-T04** — Add generation worker integration fixture covering success/failure and immutable successful results.
- [ ] **UG-AI-070** — **S12-T05** — Add cooperative cancellation checks between stages/bounded units with consistent run/stage/job states.
- [ ] **UG-AI-071** — **S12-T06** — Implement transient/permanent/cancelled retry classification and bounded backoff around generation attempts.
- [ ] **UG-AI-072** — **S12-T07** — Implement DB-authoritative outbox dispatcher recovery/claim loop and duplicate-delivery safety.
- [ ] **UG-AI-073** — **S12-T08** — Implement artifact temporary→ready→referenced publication transaction boundary and bounded orphan GC job.
- [ ] **UG-AI-074** — **S12-T09** — Add ScenarioBatch persistence/state model with 3–10 child run bound and concurrency limit.
- [ ] **UG-AI-075** — **S12-T10** — Implement deterministic batch seed/config matrix expansion.
- [ ] **UG-AI-076** — **S12-T11** — Implement exact rerun from immutable dataset/config/seed/code provenance with availability/hash validation.
- [ ] **UG-AI-077** — **S12-T12** — Generate canonical provenance manifest for data/config/code/stages/artifacts/raw metrics.
- [ ] **UG-AI-078** — **S12-T13** — Implement compare backend over persisted raw metrics/validation only; no GIS recompute.
- [ ] **UG-AI-079** — **S12-T14** — Add create/cancel/retry/progress UI over authoritative backend states with bounded polling abstraction.
- [ ] **UG-AI-080** — **S12-T15** — Add 2–N compare UI and map run switching; complete M3/M4 integration gates.
- [ ] **UG-AI-081** — **S13-T01** — Define canonical LayerCatalog entry ownership/version/source-kind/render metadata contract.
- [ ] **UG-AI-082** — **S13-T02** — Generalize bounded bbox/keyset vector API with projection/simplification policy.
- [ ] **UG-AI-083** — **S13-T03** — Implement run/dataset-scoped MVT endpoint using tile bounds + GiST prefilter + limits.
- [ ] **UG-AI-084** — **S13-T04** — Implement immutable ETag/cache-control semantics for run/dataset tiles.
- [ ] **UG-AI-085** — **S13-T05** — Implement declarative frontend layer registry for GeoJSON/bbox/MVT/raster with style separate from components.
- [ ] **UG-AI-086** — **S13-T06** — Build full layer tree from LayerCatalog/registry rather than root-component special cases.
- [ ] **UG-AI-087** — **S13-T07** — Implement asynchronous GeoJSON export job to ArtifactStore.
- [ ] **UG-AI-088** — **S13-T08** — Implement bounded multi-layer GeoPackage export job.
- [ ] **UG-AI-089** — **S13-T09** — Implement run/compare canonical raw-metrics CSV export.
- [ ] **UG-AI-090** — **S13-T10** — Export normalized config + provenance manifest + CRS/seed/dataset refs.
- [ ] **UG-AI-091** — **S13-T11** — Implement S3/MinIO ArtifactStore adapter contract parity with LocalArtifactStore.
- [ ] **UG-AI-092** — **S13-T12** — Add large-upload progress/error/retry/version UX without base64 transfer.
- [ ] **UG-AI-093** — **S13-T13** — Integrate project/datasets/map/parameters/jobs/metrics/compare into complete workspace over shared registries.
- [ ] **UG-AI-094** — **S13-T14** — Add Playwright create→upload→run→inspect→compare→export E2E fixture.
- [ ] **UG-AI-095** — **S14-T01** — Separate ingest/generation/analysis/export queues with explicit per-queue concurrency.
- [ ] **UG-AI-096** — **S14-T02** — Implement active-job/file/candidate/scenario resource bounds and backpressure responses.
- [ ] **UG-AI-097** — **S14-T03** — Profile top DB queries with EXPLAIN ANALYZE and change indexes only from evidence.
- [ ] **UG-AI-098** — **S14-T04** — Measure raster peak memory/windowing and eliminate accidental full-raster paths.
- [ ] **UG-AI-099** — **S14-T05** — Measure noding/snapping/routing timing/candidate counters on reference graphs.
- [ ] **UG-AI-100** — **S14-T06** — Measure building/infrastructure placement/accessibility hotspots and validate cache/index fixes.
- [ ] **UG-AI-101** — **S14-T07** — Build 25 km²/100 km² integrated benchmark profiles with machine metadata and budgets.
- [ ] **UG-AI-102** — **S14-T08** — Expose queue/job/stage operational metrics behind Prometheus-ready adapter.
- [ ] **UG-AI-103** — **S14-T09** — Implement artifact GC dry-run/expiry/orphan policy and bounded deletion.
- [ ] **UG-AI-104** — **S14-T10** — Add production-like Docker profile with no dev mounts and externalizable dependencies.
- [ ] **UG-AI-105** — **S14-T11** — Run upload/path/SQL/CORS/container-user security hardening audit and targeted fixes.
- [ ] **UG-AI-106** — **S14-T12** — Add documented metadata DB dump/restore smoke preserving external-artifact references.
- [ ] **UG-AI-107** — **S14-T13** — Harden migration-from-zero CI across full release integration matrix; do not duplicate existing smoke only.
- [ ] **UG-AI-108** — **S14-T14** — Add worker crash/duplicate delivery/retry/cancel/stale checkpoint/orphan artifact reliability E2E.
- [ ] **UG-AI-109** — **S14-T15** — Synchronize deployment/troubleshooting/invariants/benchmark reproduction docs with measured system.
- [ ] **UG-AI-110** — **S15-T01** — Implement experiment matrix schema that creates normal ScenarioBatch/runs rather than a parallel execution path.
- [ ] **UG-AI-111** — **S15-T02** — Package Territory A expansion dataset manifest + deterministic preprocessing.
- [ ] **UG-AI-112** — **S15-T03** — Package contrasting Territory B/from-scratch dataset manifest + preprocessing.
- [ ] **UG-AI-113** — **S15-T04** — Run same seed/config reproducibility experiment with hashes/tolerance policy.
- [ ] **UG-AI-114** — **S15-T05** — Run bounded seed-variability experiment and export canonical raw metric distributions.
- [ ] **UG-AI-115** — **S15-T06** — Run low/medium/high density scenario experiment.
- [ ] **UG-AI-116** — **S15-T07** — Run constraint-aware vs ablation baseline using canonical validation/metric outputs.
- [ ] **UG-AI-117** — **S15-T08** — Run network-aware greedy vs random/naive infrastructure baseline.
- [ ] **UG-AI-118** — **S15-T09** — Run score-weight perturbation sensitivity without GIS rerun.
- [ ] **UG-AI-119** — **S15-T10** — Implement optional real-growth holdout only if trustworthy temporal data is available; otherwise document omission.
- [ ] **UG-AI-120** — **S15-T11** — Export experiment CSV/JSON tables linked to provenance/timing/validation/raw metrics.
- [ ] **UG-AI-121** — **S15-T12** — Produce stable thesis figure/table dataset from experiment exports.
- [ ] **UG-AI-122** — **S15-T13** — Create offline demo input package using the same ingest/run path.
- [ ] **UG-AI-123** — **S15-T14** — Script and verify 5–8 minute defense flow on live system.
- [ ] **UG-AI-124** — **S15-T15** — Prepare fallback screenshots/video/reports without treating them as live-pipeline acceptance evidence.
- [ ] **UG-AI-125** — **R1-R2** — Run clean-clone and full required CI acceptance on one release candidate commit.
- [ ] **UG-AI-126** — **R3-R4** — Run full end-to-end Territory A and second-territory/generalization acceptance.
- [ ] **UG-AI-127** — **R5-R6** — Verify expansion fixed-state and same-pipeline FROM_SCRATCH invariants.
- [ ] **UG-AI-128** — **R7-R8** — Run reproducibility and performance acceptance against documented tolerance/budgets.
- [ ] **UG-AI-129** — **R9** — Verify research package contains raw metrics, manifests, experiments and report datasets.
- [ ] **UG-AI-130** — **R10** — Create v1.0.0 tag only after R1-R9 pass on the same release candidate.

## Gate rule

UG-AI-001..045 are complete, M0 and M1 are complete, and IMPLEMENTATION_READINESS is Accepted. The next task is UG-AI-046. No sprint may skip its milestone integration gate merely because its individual tasks are checked.
