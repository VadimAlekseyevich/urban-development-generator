# Spatial demographic calibration

S09-T08 converts optional S09-T07 population-raster evidence into a calibrated block
population distribution without changing authoritative demographic totals.

## Calibration scope

Calibration operates within each functional zone. A zone's population and cohort totals
remain exact; only the split between blocks in that zone may change. Jobs are not
population-raster calibrated and remain unchanged.

## Evidence blend

For each zone, block shares are blended from the existing S09-T06 block population share
and the normalized S09-T07 sampled population share. `raster_weight` controls the blend
in 0..1. Samples below `minimum_valid_fraction` do not contribute evidence. If a zone
has no positive eligible raster evidence, calibration falls back exactly to the S09-T06
distribution.

Integer zone population uses deterministic largest-remainder apportionment with stable
block-order tie breaking.

## Cohorts

After block population changes, the zone's exact age-group totals are redistributed across
the calibrated block row totals with exact integer margins. Therefore zone and project
population/cohort totals stay unchanged.

## Audit output

Each block receives original/calibrated population, sampled population, valid raster
fraction, and evidence-use status. Diagnostics report evidence coverage, fallback zones,
moved population, and maximum block shift.

## Scope boundary

T08 does not alter jobs, population targets, building capacity, raster sampling,
persistence, API, or UI. S09-T09 converts calibrated block demographics into typed
infrastructure demand.
