"""Validate/preview an experiment manifest, or submit exactly one territory.

Usage (preview requires no DB):
  uv run python -m scripts.experiment_matrix --manifest experiment.json
  uv run python -m scripts.experiment_matrix --schema

Submission is explicit and non-idempotent if a process dies between database
commit and receipt persistence; reconcile PostgreSQL before repeating.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from pydantic import ValidationError

from backend.app.application.experiment_matrix import (
    ExperimentManifest,
    ExperimentMatrixRunner,
    build_experiment_plan,
)
from backend.app.db.scenario_batch import SqlAlchemyScenarioBatchStore


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schema", action="store_true")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--territory")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--receipt", type=Path)
    args = parser.parse_args(argv)

    if args.schema:
        if args.manifest or args.execute or args.territory or args.receipt:
            parser.error("--schema cannot be combined with submission options")
        print(json.dumps(ExperimentManifest.model_json_schema(), indent=2, sort_keys=True))
        return
    if args.manifest is None:
        parser.error("--manifest is required unless --schema is selected")
    try:
        manifest = ExperimentManifest.model_validate_json(
            args.manifest.read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, ValidationError, ValueError) as exc:
        parser.error(f"invalid experiment manifest: {exc}")

    plan = build_experiment_plan(manifest)
    if not args.execute:
        if args.receipt is not None:
            parser.error("--receipt requires --execute")
        print(json.dumps(plan.as_json(), indent=2, sort_keys=True, ensure_ascii=False))
        return
    if not args.territory or args.receipt is None:
        parser.error("--execute requires one --territory and a new --receipt path")
    manifest.territory(args.territory)

    # Reserve the receipt pathname *before* the DB call to reject accidental
    # repeated invocations. A reserved/incomplete file after a crash requires
    # operator reconciliation against ScenarioBatch, Job and JobOutbox.
    try:
        with args.receipt.open("x", encoding="utf-8") as file:
            file.write(json.dumps({
                "status": "reserved",
                "experiment_id": manifest.experiment_id,
                "territory_name": args.territory,
                "plan_sha256": plan.plan_sha256,
            }, sort_keys=True) + "\n")
    except OSError as exc:
        parser.error(f"cannot reserve unique receipt path: {exc}")

    result = ExperimentMatrixRunner(
        batch_store=SqlAlchemyScenarioBatchStore()
    ).submit_territory(manifest=manifest, territory_name=args.territory)
    with args.receipt.open("w", encoding="utf-8") as file:
        json.dump(
            {"status": "submitted", **asdict(result)},
            file,
            sort_keys=True,
            indent=2,
        )
        file.write("\n")
    print(json.dumps(result.as_json(), sort_keys=True))


if __name__ == "__main__":
    main()
