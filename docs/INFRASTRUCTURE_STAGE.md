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
