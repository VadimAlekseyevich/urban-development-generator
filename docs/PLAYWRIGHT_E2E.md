# S13-T14 — Browser acceptance with Playwright (UG-AI-094)

The **Browser E2E (Playwright)** GitHub Actions job runs Chromium against the
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

**Important boundary:** `POST /uploads` does not create a DatasetVersion or
schedule data ingestion. The script `scripts/seed_browser_e2e.py` is a
**test fixture**, not an ingestion endpoint. The fixture's ready version is
not claimed to have been produced by the uploaded file. Similarly, the two
successful evaluations are fixture snapshots, **not** the result of the job
started in step 5. This acceptance gate verifies composition and persistence
at each implemented boundary; it does **not** establish a real raw-file-to-
successful-generative-run workflow. The real ingestion and calculation path
still requires separate production-level acceptance.

## Isolation and determinism

- The guarded fixture accepts only a project whose name starts with `E2E `,
  on the expected metric SRID, and with no existing datasets. It refuses to
  execute unless `UDG_E2E_FIXTURE=1` is explicitly set for that one command.
- CI starts a **disposable** Docker Compose instance, waits for API readiness,
  then runs one serial browser test and always tears the stack down with
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
