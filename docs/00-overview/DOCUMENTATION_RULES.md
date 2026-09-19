# Documentation Rules

> **Status: Accepted**
>
> These rules govern architecture and implementation documentation for Urban Development Generator.

## 1. One canonical owner per contract

A technical decision or behavioral contract has exactly one canonical document. Other documents may summarize it, but must link to the canonical owner instead of redefining it.

Canonical ownership:

- product/scope/release requirements -> `docs/DEVELOPMENT_PLAN.md`;
- domain/data schema -> `docs/DATA_MODEL.md` and core domain types;
- API transport contract -> `docs/API.md`;
- stable system boundaries -> `docs/ARCHITECTURE.md`;
- pipeline/stage execution semantics -> `docs/02-architecture/PIPELINE_MODEL.md`;
- implementation ordering -> `docs/IMPLEMENTATION_VERSION_ROADMAP.md`;
- one-request-sized implementation work -> `docs/07-planning/AI_EXECUTION_TASKS.md`;
- meaningful trade-offs -> ADRs under `docs/adr/`;
- current blockers/readiness -> `docs/07-planning/IMPLEMENTATION_READINESS.md`;
- discovered architecture debt -> `docs/07-planning/ARCHITECTURE_DEBT_AUDIT.md`.

## 2. Document status

Use one of:

- Placeholder
- Draft
- Accepted
- Implemented
- Deprecated
- Historical

Accepted means implementation must follow the contract unless an ADR changes it.
Implemented means code and tests currently prove the accepted contract.
Historical documents are not implementation authority.

## 3. Architecture-changing implementation rule

If implementation demonstrates that an Accepted contract is impossible, materially harmful, or inconsistent:

1. stop at the affected boundary;
2. record evidence;
3. create/update an ADR;
4. update the canonical specification and dependent roadmap entries;
5. only then implement the changed contract.

Do not hide architecture changes inside a feature work item.

## 4. Subsystem specification minimum

A canonical subsystem spec should state, where applicable:

1. problem and goal;
2. user-visible behavior;
3. requirements;
4. non-goals;
5. input/output contracts;
6. ownership/versioning;
7. CRS/units;
8. public boundaries/ports;
9. failure and edge cases;
10. determinism;
11. performance bounds;
12. persistence/artifact behavior;
13. tests/fixtures;
14. Definition of Done.

## 5. AI task rule

A task may be sent to an AI/developer only when its entry in `AI_EXECUTION_TASKS.md` identifies:

- parent roadmap work item;
- prerequisites;
- canonical documents to read;
- exact goal and output contract;
- explicit non-goals;
- architecture boundaries that may not change;
- failure/edge cases;
- determinism/CRS/performance constraints where relevant;
- required tests and completion evidence.

If those are unknown, create a contract/stabilization task first.

## 6. Documentation update rule

For every meaningful change:

- behavior/schema/architecture change -> update canonical spec;
- trade-off change -> ADR;
- new architecture risk/debt -> debt audit/risk register;
- implementation completion -> execution task and readiness state;
- no copy-pasted competing definitions.

## 7. Roadmap rule

A roadmap item describes a capability, not permission to invent missing architecture.

Future work must extend existing contracts when they already exist. A roadmap item must never silently introduce a second:

- Stage model;
- network abstraction;
- metric vocabulary;
- constraint engine;
- artifact lifecycle;
- job/outbox state machine;
- fixed/generated ownership model.

## 8. Status reporting

Progress percentages based on work-item counts are informational only. Architecture readiness and milestone completion are separate gates and must not be inferred from item count.
