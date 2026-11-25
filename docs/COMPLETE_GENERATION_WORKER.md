# Complete worker composition (#202)

GenerationWorkerSettings and LegacyWorkerSettings install the real generation
service at ARQ startup. The run-scoped factory assembles the persisted snapshot
and strict canonical-stage-configs-v5 config (all twelve stages), builds the
registry and uses existing transactional materialization. No partial registry
is deployed. Earlier schema versions remain available for offline validation;
they are not a complete runtime. The final manifest is published only after
validation and metrics; HARD violations prevent success. The API export remains
the successful-run projection including the final stage artifact.

Still required: actual uploaded-data acceptance via API and browser, UI form
for v5 configuration, verification of pinned code revision and full real-city
fixture coverage. Existing population/GFA and fixed-zone hydration limitations
remain. This PR is not release acceptance.
