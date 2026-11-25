# Population allocation

S09-T03 turns fractional S09-T02 residential capacity into deterministic integer
generated-resident assignments. It never exceeds building capacity and never emits
NaN/Inf.

## Target resolution

The allocator accepts an optional non-negative `baseline_population`.

For an absolute `total_population` target:

```text
generated target = max(total target - baseline population, 0)
```

When no baseline is supplied, baseline is zero, which is the from-scratch case.

For a `growth_rate` target, baseline population is required. The target total is
`baseline × (1 + growth_rate)`, rounded to the nearest integer with explicit
ROUND_HALF_UP semantics. Generated target is then the positive difference from baseline.

If the resolved target is below the baseline, the allocator assigns zero new residents and
returns `BASELINE_EXCEEDS_TARGET` with an explicit excess diagnostic rather than trying
to model demolition or depopulation.

## Integer capacity and apportionment

Each fractional T02 resident capacity is converted to a non-negative integer ceiling for
allocation by flooring with a tiny numeric tolerance. The project-wide generated target is
capped by the sum of those integer capacities.

Residents are apportioned proportionally to integer building capacity using exact integer
quotient/remainder arithmetic (Hamilton/largest-remainder style). Equal remainders are
resolved by stable `building_id`, so input ordering cannot change the result.

Per-building `utilization_ratio` is zero when integer capacity is zero; division by zero,
NaN and Inf are therefore impossible.

## Diagnostics

The result records:

- resolved target total population;
- baseline population;
- generated population target;
- allocatable generated capacity;
- allocated and unmet generated population;
- baseline excess when the target is below existing population;
- `TARGET_MET`, `CAPACITY_EXHAUSTED`, or `BASELINE_EXCEEDS_TARGET`.

## Scope boundary

S09-T03 does not allocate age groups, estimate jobs, aggregate by block/zone, sample
population rasters, persist results, or expose API/UI. Age composition remains S09-T04.
