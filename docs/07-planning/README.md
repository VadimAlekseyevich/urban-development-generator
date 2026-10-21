# 07 — Planning

> **Current gate:** UG-AI-107 merged; UG-AI-108 (PR #196) passed CI, awaits review.
> UG-AI-109 documentation is stacked on that branch. M5 and M6 remain open.
>
> Stabilization evidence commit: `39eaa1688e06669b0e01e999304710873fd9cf0e`
>
> Readiness: **ACCEPTED**

- [IMPLEMENTATION_READINESS.md](IMPLEMENTATION_READINESS.md) — accepted architecture-to-feature gate and current next action.
- [ARCHITECTURE_STABILIZATION_PLAN.md](ARCHITECTURE_STABILIZATION_PLAN.md) — completed finite M0-01..M0-10 work order and exit evidence.
- [ARCHITECTURE_DEBT_AUDIT.md](ARCHITECTURE_DEBT_AUDIT.md) — closed Critical/High debt ledger and named Medium future owners.
- [M0_ARCHITECTURE_REGRESSION_GATES.md](M0_ARCHITECTURE_REGRESSION_GATES.md) — CI-enforced architecture invariants.
- [M0_CROSS_CUTTING_INVARIANTS.md](M0_CROSS_CUTTING_INVARIANTS.md) — invariant ownership/evidence.
- [M0_PERSISTENCE_ORCHESTRATION_BOUNDARY.md](M0_PERSISTENCE_ORCHESTRATION_BOUNDARY.md) — S12 compatibility proof.
- [ROADMAP_AUDIT.md](ROADMAP_AUDIT.md) — final architecture review of S10–S15 and release gates.
- [MILESTONES.md](MILESTONES.md) — integrated capability gates M0–M8.
- [AI_EXECUTION_TASKS.md](AI_EXECUTION_TASKS.md) — ordered one-request-sized execution backlog.
- [RISKS.md](RISKS.md) — living architecture/delivery risk register.
- [DECISION_LOG.md](DECISION_LOG.md) — compact accepted-decision index.

Capability roadmap remains at `docs/IMPLEMENTATION_VERSION_ROADMAP.md`.

Follow the earliest unchecked item in `AI_EXECUTION_TASKS.md`. Do not
skip UG-AI-108 review/merge because UG-AI-109 documentation is available
in a stacked PR. For deployment, troubleshooting and benchmark reproduction
see [the operations runbook](../OPERATIONS_RUNBOOK.md).
