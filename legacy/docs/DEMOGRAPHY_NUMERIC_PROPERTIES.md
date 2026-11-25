# Demography numeric and property hardening

S09-T11 closes the demography sprint with cross-module numeric/property invariants rather
than adding new production behavior.

The property suite verifies:

- zero-GFA / zero-capacity inputs stay finite and report unmet target explicitly;
- mixed-use GFA is partitioned consistently between residential capacity and job-supporting
  area;
- integer population allocation hits the exact target whenever capacity is sufficient;
- age-group allocation preserves exact population/cohort sums for multiple target sizes;
- all-nodata raster evidence is neutral and deterministic;
- the full capacity → population → cohorts → employment → block/zone aggregation pipeline
  is invariant to input permutation.

These tests complement the module-level tests from S09-T02 through S09-T10 and enforce the
Sprint S09 gate that demographic constraints are represented by explicit domain models and
stable invariants rather than a single residents formula.
