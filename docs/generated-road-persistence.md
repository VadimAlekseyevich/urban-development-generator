# Generated road persistence — S06-T15

`SqlAlchemyGeneratedRoadWriter` atomically replaces the generated road edges owned by one unfinished generation run. It persists metric `LINESTRING` geometry and explainable road metadata in `generated_roads.attributes_json`, including stable edge/road ids, part index, length, generated class/origin/reason, endpoint node ids, source-road UUID references, and classifier provenance.

Source-road references are validated against `source_roads` before any replacement write. Successful generation runs remain immutable. Writes are chunked by a configurable positive row limit and execute in one transaction.

`generated_roads` keeps its existing `(run_id, id)` and GiST geometry indexes. S06-T15 adds run-scoped expression indexes for `road_id` and unique `edge_id`, plus a GIN index over `source_road_ids` for provenance lookups.

API/UI exposure remains S06-T16.
