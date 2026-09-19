# Demography metrics, API and UI

S09-T10 turns the S09 domain outputs into stable observable metrics and a bounded map
read-model.

## Metrics

`DemographyMetricsBuilder` combines the authoritative S09 block aggregation with
authoritative metric block areas. It derives:

- block and project population;
- population density per km²;
- approximate jobs;
- age-group resident counts and shares.

Zero population produces zero age shares and zero density where appropriate; NaN/Inf are
rejected.

## Persistence read-model

No new spatial table is introduced. The existing JSONB extension points are used:

- `GeneratedBlock.attributes_json.demography` stores block population, density, jobs and
  age groups;
- `GenerationRun.metrics_json.demography` stores project/run totals and provenance.

Existing JSON keys are preserved. The writer requires an exact match between persisted
`block_key` values and the demographic aggregation and refuses to mutate successful
generation runs.

## API

The v1 endpoints are:

- `GET /projects/{project_id}/demography-runs`;
- `GET /projects/{project_id}/demography-runs/{run_id}/metrics`;
- `GET /projects/{project_id}/demography-runs/{run_id}/blocks/geojson`.

The GeoJSON endpoint is bbox/limit bounded and exposes flat `population`,
`population_density_per_km2`, and `jobs_estimate` properties for map styling. Age
groups remain available as structured inspector data.

## UI

The Demography panel provides:

- run selection;
- project totals for population, density, jobs and block count;
- age-group shares;
- choropleth modes for density, population and jobs;
- block inspector with population, density, jobs and cohorts.

The panel follows the same viewport-loading model as roads, blocks and buildings.

## Scope boundary

T10 is an observability/delivery layer. It does not change S09 allocation/calibration or
define S10 infrastructure demand coefficients. Numeric/property hardening remains S09-T11.
