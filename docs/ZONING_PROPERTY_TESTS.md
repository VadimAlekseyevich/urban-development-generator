# Zoning property tests

S05-T10 closes the functional-zoning sprint with invariant-focused tests spanning the existing zoning core. The task adds no production algorithm, persistence, API, or UI behavior.

## Property matrix

`tests/unit/test_zoning_properties.py` exercises deterministic families of zoning inputs rather than one hand-picked fixture.

The matrix verifies:

- valid polygonal partition output across multiple generated seed layouts;
- no positive-area overlap between generated partition cells;
- complete coverage of the supplied developable area within the partition contract tolerance;
- clipping for concave developable geometry;
- preservation of holes/excluded area;
- coverage of disconnected multipart developable geometry;
- target-share allocation tolerance for indivisible cells;
- deterministic partition, assignment, and bounded refinement results;
- the expansion-mode gate that fixed existing-zone references remain unchanged while generated zoning is limited to the supplied developable-area delta.

## Target-share tolerance

Functional-zone assignment operates on indivisible partition cells. Exact configured area shares are therefore not always representable.

The property test uses equal-area cells and requires every class to stay within one cell area of its configured target. This expresses the discrete allocation limit directly instead of imposing an arbitrary percentage tolerance. Refinement is run with no adjacency pressure and a minimum area below one cell, so it may improve target error but cannot legitimately trade target accuracy for a higher-priority zoning objective in this test.

For irregular production partitions, diagnostics continue to report the exact target and achieved area/share so callers can evaluate the error relative to actual cell granularity.

## Determinism

Generated cases use fixed local `random.Random` seeds only to construct repeatable test inputs. Production zoning does not consume this RNG. The same input geometry, seed order, suitability scores, config, and iteration budget must produce identical:

- partition cell WKB in seed order;
- coverage/overlap diagnostics;
- assigned zone classes and share diagnostics;
- refinement objective, iteration count, stop reason, and final assignment.

## Sprint S05 gate

Existing zoning remains represented by immutable fixed source references. The generation stages receive only a developable-area geometry and produce generated cells over that geometry. The gate property confirms those fixed refs are unchanged before and after generated zoning and that the generated union equals the supplied developable area.

No city-specific assumptions are part of these properties.
