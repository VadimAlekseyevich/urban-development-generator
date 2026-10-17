"""Operator-safe S14 artifact GC audit and bounded execution.

Default is preview-only even when worker cron is configured to delete:
    uv run python -m scripts.artifact_gc --max-batch 20
Deletion requires an explicit --execute flag:
    uv run python -m scripts.artifact_gc --execute --max-batch 20
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import timedelta

from backend.app.adapters import get_runtime_artifact_store
from backend.app.core.config import settings
from backend.app.db.artifact_gc import SqlAlchemyArtifactGc


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--execute", action="store_true",
        help="Apply deletion (default: non-destructive dry-run).",
    )
    parser.add_argument(
        "--min-age-hours", type=int, default=settings.artifact_gc_min_age_hours,
    )
    parser.add_argument("--max-batch", type=int, default=settings.artifact_gc_max_batch)
    parser.add_argument("--max-scan", type=int, default=settings.artifact_gc_max_scan)
    args = parser.parse_args()
    result = SqlAlchemyArtifactGc(store=get_runtime_artifact_store()).collect(
        min_age=timedelta(hours=args.min_age_hours),
        max_batch=args.max_batch,
        max_scan=args.max_scan,
        dry_run=not args.execute,
    )
    print(json.dumps(asdict(result), sort_keys=True))


if __name__ == "__main__":
    main()
