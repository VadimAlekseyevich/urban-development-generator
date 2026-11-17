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
    CanonicalStageConfigResolver,
    PersistedStageConfigError,
)
from core.urban_generator.domain import ConfigRef


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schema", action="store_true")
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args(argv)
    resolver = CanonicalStageConfigResolver()
    if args.schema:
        if args.manifest is not None:
            parser.error("--schema and --manifest cannot be combined")
        print(json.dumps(resolver.json_schema(), ensure_ascii=False, indent=2))
        return
    if args.manifest is None:
        parser.error("provide --manifest or --schema")
    try:
        data = json.loads(args.manifest.read_text(encoding="utf-8"))
        bindings = resolver.resolve(
            config_json=data,
            schema_version=CONFIG_SCHEMA_VERSION,
            source=ConfigRef(name="generation", ref="local-config-validation"),
        )
    except (OSError, UnicodeError, ValueError, TypeError, PersistedStageConfigError) as exc:
        parser.error(f"invalid canonical config: {exc}")
    print(json.dumps({
        "valid": True,
        "schema_version": CONFIG_SCHEMA_VERSION,
        "typed_stage_names": [item.stage_name for item in bindings],
    }, sort_keys=True))


if __name__ == "__main__":
    main()
