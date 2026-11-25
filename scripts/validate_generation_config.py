"""Inspect schema or validate a persisted core Stage configuration offline.

This is a typed configuration check, *not* a production generation execution
path. It does not create projects, runs, stages, artifacts or Redis tasks.

Examples:
  uv run python -m scripts.validate_generation_config --schema
  uv run python -m scripts.validate_generation_config --manifest /private/config.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from backend.app.adapters.stage_config_resolver import (
    CONFIG_SCHEMA_VERSION,
    EVALUATION_CONFIG_SCHEMA_VERSION,
    INFRASTRUCTURE_CONFIG_SCHEMA_VERSION,
    PREPARED_CONFIG_SCHEMA_VERSION,
    CanonicalStageConfigResolver,
)
from core.urban_generator.domain import ConfigRef


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schema", action="store_true")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument(
        "--schema-version", choices=(
            CONFIG_SCHEMA_VERSION, PREPARED_CONFIG_SCHEMA_VERSION,
            INFRASTRUCTURE_CONFIG_SCHEMA_VERSION, EVALUATION_CONFIG_SCHEMA_VERSION,
        ),
        default=CONFIG_SCHEMA_VERSION,
    )
    args = parser.parse_args(argv)
    resolver = CanonicalStageConfigResolver(schema_version=args.schema_version)
    if args.schema:
        if args.manifest is not None:
            parser.error("--schema and --manifest cannot be combined")
        print(json.dumps(resolver.json_schema(), ensure_ascii=False, indent=2))
        return
    if args.manifest is None:
        parser.error("provide --manifest or --schema")
    try:
        data = json.loads(
            args.manifest.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
        bindings = resolver.resolve(
            config_json=data,
            schema_version=args.schema_version,
            source=ConfigRef(name="generation", ref="local-config-validation"),
        )
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        parser.error(f"invalid canonical config: {exc}")
    print(json.dumps({
        "valid": True,
        "schema_version": args.schema_version,
        "typed_stage_names": [item.stage_name for item in bindings],
    }, sort_keys=True))


if __name__ == "__main__":
    main()
