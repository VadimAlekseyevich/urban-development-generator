# Generated road persistence — S06-T15

S06-T15 persists only generated road edges from the metric-CRS `RoadGraph`. Fixed/source roads remain in canonical source tables and are never copied into `generated_roads`.

`SqlAlchemyGeneratedRoadWriter.replace()` performs a run-scoped atomic replace. It locks the `GenerationRun`, rejects successful immutable runs, validates the graph working SRID against the run, deletes prior generated-road rows for unfinished retries, then inserts new rows in bounded chunks.

Each `generated_roads` row stores its `LineString` geometry and a stable semantic payload in `attributes_json`: `edge_id`, `road_id`, `part_index`, metric `length_m`, generated `road_class`, generation `origin`, `classification_reason`, endpoint node identifiers, source-road UUID references, and the classification strategy name/version.

Source references are explicit UUIDs of canonical `source_roads`. The writer verifies that every referenced UUID exists before deleting or inserting any run rows. Missing refs fail the transaction.

The existing generic generated-entity schema already provides the `run_id` foreign key, `(run_id, id)` access index, and GiST geometry index. Migration `0014_generated_road_persistence` adds run-scoped expression indexes for `road_id` and `road_class`, a run-scoped unique expression index for stable `edge_id`, plus a GIN index for `source_road_ids` JSON containment queries.

Road API/UI presentation remains S06-T16. This item does not persist source roads or change source-state ownership semantics.
