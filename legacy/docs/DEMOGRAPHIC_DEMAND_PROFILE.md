# Demographic demand profile

S09-T09 is the stable handoff from demography to the infrastructure stage. It exposes
typed demographic signals by block without embedding any concrete infrastructure category
or facility rule.

## Signals

Every block and the project total expose the same canonical signal schema:

- population — people;
- one age-group signal per configured demographic group — people;
- workforce — people;
- jobs — jobs.

Age-group signals retain stable group code and age bounds. All values are finite and
non-negative. Workforce remains fractional and is derived from the current block population
times the scenario working-population ratio.

## Infrastructure boundary

The demand profile intentionally does not define education, healthcare, retail, recreation,
or their demand coefficients. S10-T01 owns InfrastructureType and its demand model. An
infrastructure type can therefore consume population, a named cohort, workforce, jobs, or
a later combination without creating a reverse dependency from demography to infrastructure.

This also means a spatially calibrated S09-T08 aggregation can be passed directly to the
builder: the resulting demand follows calibrated block population/cohorts while preserving
the upstream jobs signal.

## Consistency and provenance

The builder requires exact scenario version/fingerprint agreement with the aggregation and
requires age-group metadata to match the scenario. Every block must expose the same signal
keys as project totals, and block values are verified to sum back to project totals within
floating-point tolerance.

Work is bounded by max_blocks and output order is stable by block ID / signal category /
age-group order.

## Scope boundary

S09-T09 does not calculate unmet infrastructure demand, apply facility-specific coefficients,
persist results, expose HTTP endpoints, or render UI. Those responsibilities begin in S10
and S09-T10 respectively.
