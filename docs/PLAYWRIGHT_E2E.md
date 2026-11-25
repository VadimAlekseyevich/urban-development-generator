# S13-T14 — Browser acceptance with Playwright (UG-AI-094)

The **Browser E2E (Playwright)** GitHub Actions job runs two independent Chromium journeys. `frontend/e2e/ingest.spec.mjs` creates a project and saves its GeoJSON boundary, uploads a real GeoJSON file through the browser, submits the project-scoped ingest Job/Outbox, waits for the deployed ingest worker to publish a ready DatasetVersion, and reads normalized source roads from PostGIS **without any DB fixture**. `frontend/e2e/workspace.spec.mjs` retains the earlier synthetic evaluated-run comparison fixture.

The existing comparison journey still runs Chromium against the
real Docker Compose frontend, API, PostgreSQL/PostGIS, artifact storage,
dispatcher and worker services. The scenario follows:

1. Create a project in React with an explicit metric working SRID.
2. Send `tests/fixtures/ingest/roads.geojson` as a **binary multipart upload**,
   and verify the returned ready Artifact and SHA-256.
3. Seed an isolated, synthetic ready DatasetVersion, one PostGIS road and
   two canonical persisted/evaluated runs into the **project created by the
   browser**; select the version from the real project-scoped listing.
4. Reload the bookmarked map URL, read the real PostGIS source road through
   FastAPI, and click its rendered MapLibre feature in Chromium.
5. Create a real queued generation Run and verify its authoritative Job
   returned by the API; the dispatcher/worker may advance or fail this
   separate job according to its normal rules.
6. Compare the two synthetic, previously evaluated immutable runs through
   the real compare API and download the actual canonical metrics CSV.

**Important boundary:** `POST /uploads` by itself does not create a DatasetVersion or
schedule data ingestion. The script `scripts/seed_browser_e2e.py` is a
**test fixture**, not an ingestion endpoint. The fixture's ready version is
not claimed to have been produced by the uploaded file. Similarly, the two
successful evaluations are fixture snapshots, **not** the result of the job
started in step 5. The comparison acceptance gate verifies composition and persistence
at each implemented boundary; it does **not** establish a real raw-file-to-
successful-generative-run workflow. The unseeded ingestion path is covered by the separate ingest journey;
the later unseeded generation tests below verify the real successful calculation path.

## Isolation and determinism

- The guarded fixture accepts only a project whose name starts with `E2E `,
  on the expected metric SRID, and with no existing datasets. It refuses to
  execute unless `UDG_E2E_FIXTURE=1` is explicitly set for that one command.
- CI starts a **disposable** Docker Compose instance, waits for API readiness,
  then runs serial browser tests and always tears the stack down with
  `docker compose down -v`. Never run this against a shared database.
- Browser tests replace **only** the public demotiles basemap style with
  a local minimalist MapLibre style to eliminate third-party outages.
  They do **not** mock any application REST endpoint.
- The seed uses exact 4326→32637 PostGIS geometry and canonical
  `CompositeScoreResult`/validation serializers; API comparison and export
  read persisted data without GIS recomputation.
- Exact API IDs are checked, along with project/run owner scope, a real
  ready Artifact checksum, persisted road properties, comparison metrics,
  downloadable CSV data and URL restoration.
- The job is bounded to one Chromium worker with Playwright screenshots
  and traces saved as GitHub CI artifacts on failure.

## Running locally

With Docker, Docker Compose, Node 22 and Chromium prerequisites available:

```bash
cp .env.example .env
export GENERATION_BUILD_REVISION=$(git rev-parse HEAD)
docker compose up -d --build
cd frontend
npm ci --no-audit --no-fund
npm install --no-save --package-lock=false @playwright/test@1.56.1
npx playwright install --with-deps chromium
npm run test:e2e
cd ..
docker compose down -v
```

The Playwright runner is pinned in the installation command so the original
frontend production dependencies and lockfile are unchanged. Browser tests
are not included in the fast `npm test`; they are a separate mandatory CI
gate. Run only against an **isolated** stack because the fixture inserts
synthetic test datasets and successful runs into the project's database.


## Unseeded worker acceptance (P0)

`frontend/e2e/generation-acceptance.spec.mjs` adds a separate HTTP-driven
Playwright test to the existing Compose browser job. It creates a project,
sets a WGS84 boundary, uploads original vector bytes and a metric GeoTIFF,
submits actual ingest jobs and waits for ready versions. A normal run
submission uses the four returned version IDs, actual checkout SHA and
the small deterministic v5 smoke config with an additional positive slope
factor. The real dispatcher and generation worker must finish all twelve
stages; the test checks nonempty GIS endpoints, metrics and the downloadable
provenance manifest including the normalized DEM reference. A terminal
failure or timeout is a test failure, not a skipped or seeded success.

The source-only helper `scripts/generation_acceptance_inputs.py` generates
a tiny artificial 10 m square and DEM; it does not connect to a database
or fabricate any run/output. This is an engineering acceptance gate for
EXPANSION, not a real-city quality benchmark or proof of the neighbourhood
planning preset. A second acceptance case uses a 100 m square and only DEM/landuse source
versions in FROM_SCRATCH, requiring generated roads, blocks, parcels, buildings
and facilities without fixed urban state. Both modes must finish all twelve
stages; empty layers are failures. Both modes passed the exact-head #222 CI.
Browser interaction with actual successful outputs, revision enforcement and
representative real territories remain separate acceptance work. The test is not claimed to pass until CI confirms it.


The first FROM_SCRATCH attempt failed at demographic persistence after seven
successful stages: building-derived aggregation omitted unbuilt blocks.
The fix completes associated block aggregates with explicit zero population,
jobs and age cohorts; blocks lacking zone identity remain unassociated.
Exact matching remains required for all associated blocks. The same end-to-end
assertions remain mandatory and no success is claimed before a green rerun.


The second FROM_SCRATCH attempt completed all twelve stages but returned
zero buildings. The nonempty layer assertion remains mandatory. The next
attempt uses a 20 m routing grid, bounded 40 growth iterations and 2 m
building candidate spacing, with explicit discrete placement tolerances.
No hard GIS validation is relaxed. Final validation now rejects a positive
generated population target with zero allocation; stage diagnostics are
exposed in run control so a user can see the reason. Partial positive
allocation remains an explicit shortfall, not a universal failure.


## Planning profile and replay acceptance (new gate)

The 100 m FROM_SCRATCH smoke scenario passed alongside EXPANSION in CI
45684956177. The new 400 m square test imports real DEM and landuse bytes,
uses the browser planning form (40 m cells, target 300 residents, other
profile fields unchanged), submits the actual configuration, waits for all
stages, checks nonempty spatial layers, pins the new result without reload
and verifies the building viewport reads that run. The public background
style alone is replaced offline. A second same-seed FROM_SCRATCH run must
match complete geometry multisets and score metrics, not run UUIDs or
timestamps. No database seeding. All five browser journeys passed in #224 CI 45689793646: the profile produced 16 zones, 171 road features, 95 blocks, 80 parcels, 9 buildings and one facility; same-seed geometry and score replay passed. The PR is merged.


## Quality calibration and actual export acceptance (candidate v2)

The v1 profile has many small blocks and only nine buildings on the 400 m
square. The candidate v2 profile bounds extra road routing attempts (40 by
default), exposes the square footprint side (10 m by default), derives the
minimum lot area as max(200, ceil(side² / target coverage)), and samples
building anchors at footprint-side spacing. All geometry and spacing gates
remain unchanged. The E2E test reconstructs the v1 parameters, runs both
with the same inputs, seed and executable revision, logs actual population,
target attainment and infrastructure metrics, and requires a strict building
count improvement and no population regression. More buildings alone do not
establish urban quality. It compares the two real completed runs in Chromium,
downloads their metrics CSV, and checks a real asynchronous GeoPackage job
for buildings and roads, feature counts, downloaded byte length and SHA-256.
The v2 candidate is not yet accepted; see #202.
