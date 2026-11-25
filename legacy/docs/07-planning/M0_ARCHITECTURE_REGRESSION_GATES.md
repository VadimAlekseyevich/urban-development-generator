# M0 Architecture Regression Gates

> **Gate:** M0-09
>
> These checks turn the accepted M0 architecture into executable merge gates. They are not a
> second test suite: every file below runs under the existing required `uv run pytest` step in
> pull-request CI.

## Gate matrix

| Invariant | Required automated evidence |
| --- | --- |
| core has no HTTP/ORM/Redis/worker dependency | `tests/unit/test_architecture_boundaries.py::test_core_does_not_import_application_or_infrastructure_frameworks` |
| legacy second pipeline cannot return | `tests/unit/test_architecture_boundaries.py::test_legacy_second_pipeline_model_does_not_return` |
| NetworkX remains behind its adapter | `tests/unit/test_architecture_boundaries.py::test_networkx_is_confined_to_the_network_adapter` |
| infrastructure snapping cannot import NetworkX or `SpatialSnapIndex` directly | `tests/unit/test_architecture_boundaries.py::test_infrastructure_network_snap_does_not_bypass_network_backend` |
| canonical stage catalog is unique and dependency ordered | `tests/unit/test_stage_catalog.py::test_canonical_stage_catalog_is_unique_and_dependency_ordered` |
| every implemented adapter satisfies `Stage` and catalog dependencies | `tests/unit/test_stage_catalog.py::test_all_implemented_stage_adapters_satisfy_canonical_protocol` |
| persisted stage identity matches canonical metadata and output provenance | `tests/unit/test_stage_persistence_boundary.py` |
| typed stages compose through demography without infrastructure frameworks | `tests/integration/test_in_memory_generation_spine.py` |
| full spine rerun is deterministic | `tests/integration/test_in_memory_generation_spine.py::test_expansion_spine_is_deterministic_end_to_end` |
| fixed/generated road ownership is preserved | `tests/unit/test_road_stage_adapter.py` and full spine |
| fixed building state participates in EXPANSION without mutation | `tests/unit/test_building_stage_adapter.py::test_building_stage_expansion_preserves_fixed_refs_and_spacing_state` |
| demographic baseline participates in EXPANSION without mutation | `tests/unit/test_demography_stage_adapter.py::test_demography_stage_expansion_preserves_fixed_refs_and_baseline` |
| FROM_SCRATCH rejects/omits fixed state using the same stage family | existing road/building/demography Stage adapter tests |
| migration chain carries Stage output provenance from zero | `tests/integration/test_persistence_database.py` plus CI migration smoke |

## CI enforcement

The repository `.github/workflows/ci.yml` runs on every pull request and the Python job executes:

```text
ruff
mypy backend core worker
pytest
road reference benchmark
block/parcel reference benchmark
building reference benchmark
alembic upgrade head -> downgrade base -> upgrade head
```

Frontend typecheck/build and the full Docker Compose smoke are separate required workflow jobs in
the same CI workflow.

Because the architecture checks above live inside the ordinary pytest collection, a failed
architecture invariant fails the Python job. There is no opt-in marker or separate command that a
normal PR can accidentally omit.

## Change rule

Do not weaken, skip, rename away, or delete an architecture gate merely to make a feature PR green.

If a future Accepted ADR intentionally changes an invariant:

1. update the canonical architecture document and ADR;
2. update the relevant regression test in the same change;
3. update this matrix;
4. keep the replacement gate at least as enforceable as the previous one.

## M0-09 exit

M0-09 is complete only when:

- the protocol-conformance gate is present;
- every matrix entry points to an automated test or CI step;
- the complete Python/frontend/Compose workflow is green on the same M0-09 commit.
