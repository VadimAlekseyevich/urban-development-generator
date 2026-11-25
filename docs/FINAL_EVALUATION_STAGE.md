# Final validation and metrics (#202)

The canonical final_validation Stage consumes typed outputs of the earlier
nine stages. It aggregates zoning rule results, converts road validation
issues to HARD results with available edge geometry, checks generated building
and standalone infrastructure-site geometry against the project boundary
and hard geometry layers, and runs the configured canonical aggregate bounds
and soft preferences using RegisteredConstraintEngine. Accepted host facilities
reuse the checked generated building geometry. Intermediate rejected placement
candidates are not reported as invalid final objects.

Land/building, road, demography and infrastructure raw metrics are derived
using existing adapters; constraint metrics are derived from the combined
ValidationReport. The metrics Stage uses explicit normalization and score
weights. The existing SQL writers persist both results in the authoritative
stage completion transaction. The score envelope persists the configured
subset, not every distribution/diagnostic. Missing scalar values are not
fabricated. A report with hard failures remains available for diagnosis;
the forthcoming persist_manifest/final completion gate must prevent a
successful run with HARD failures. This is not deployed as a partial runtime.

Spatial validation is not a complete jurisdiction-specific planning-code
checker. Earlier placement and topology algorithms remain responsible for
spacing, frontage and generated-object construction; this final stage checks
specified aggregate and hard spatial evidence, not arbitrary local rules.
