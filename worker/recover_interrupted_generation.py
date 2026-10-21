"""Explicit, operator-fenced reconciliation for workers that died mid-run.

Example (only after all generation worker processes have been stopped):
    python -m worker.recover_interrupted_generation --run-id UUID \
        --stale-minutes 60 --confirm-workers-stopped --execute

No automatic retries, no Redis reenqueues and no synthetic stage results.
"""

from __future__ import annotations

import argparse
import uuid
from datetime import UTC, datetime, timedelta

from backend.app.db.generation_state import SqlAlchemyGenerationStateStore


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", type=uuid.UUID, required=True)
    parser.add_argument("--stale-minutes", type=int, default=60)
    parser.add_argument("--confirm-workers-stopped", action="store_true")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if args.stale_minutes < 30:
        parser.error("--stale-minutes must be at least 30")
    if not args.execute:
        print("No changes made (dry run). Stop workers and pass --execute to reconcile.")
        return
    if not args.confirm_workers_stopped:
        parser.error("--execute requires --confirm-workers-stopped")
    cutoff = datetime.now(UTC) - timedelta(minutes=args.stale_minutes)
    state = SqlAlchemyGenerationStateStore().finalize_abandoned(
        run_id=args.run_id,
        stale_before=cutoff,
        workers_stopped=True,
    )
    print(f"run {args.run_id}: {state}")


if __name__ == "__main__":
    main()
