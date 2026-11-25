# Canonical infrastructure Stage (#202)

The Stage wraps existing S10 algorithms, not another placement engine.
The input resolver must supply the actual final-road NetworkBackend,
demographic demand, generated block/parcel/building objects, selected
existing facility records and fixed building footprints. Type and capacity
assumptions are explicit; missing/unsupported mapping fails unless the
caller chooses skip_unmapped. The nearest-existing facility capacity is
allocated in stable block/type key order. The canonical metrics builder
replays generated service and reports unmet demand, not artificial coverage.

Types run in stable type-code order. Site candidates intersecting building
footprints or existing facility geometry are rejected conservatively.
Selected locations cannot be reused across types; host-building candidates
retain existing geometry and do not change the building's assigned use.
This is a conservative feasibility rule, not an optimized joint siting solver.

Integration still requires PostGIS facility hydration, per-run network port
composition, persisted full generation config, transactional generated
infrastructure writer and final validation/metrics/manifest/worker.
The tests exercise actual candidate generation and routing against explicit
fixture geometry; they do not prove deployed full generation.

## Runtime binding

The selected run-linked PostGIS facility reader verifies exact version set,
project/boundary/CRS, and bounded intersection with the project boundary.
It preserves missing capacity as None; a mapping rule or configured type
capacity is then used by the existing adapter. Explicit zero capacity is
rejected rather than silently treated as active service. A selected layer
with no intersecting features is valid. The final generated/fixed road graph
is converted by NetworkXBackend.from_road_graph. Stage output is written
through generated infrastructure writer savepoints in the authoritative
stage completion transaction. No deployed worker is yet installed.
