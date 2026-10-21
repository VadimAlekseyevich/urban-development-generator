"""Read-only operations-documentation drift check; no DB or network usage."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import unquote

from worker.queues import LEGACY_QUEUE, REDIS_QUEUES

ROOT = Path(__file__).resolve().parents[1]
RUNBOOK = Path("docs/OPERATIONS_RUNBOOK.md")

OWNER_DOCS = (
    "PRODUCTION_COMPOSE.md",
    "SECURITY_AUDIT_S14_T11.md",
    "WORKER_QUEUES.md",
    "JOB_ADMISSION.md",
    "RUN_CONTROL.md",
    "INGEST_SUBMISSION.md",
    "OPERATIONAL_METRICS.md",
    "ARTIFACT_GC_POLICY.md",
    "GENERATION_CRASH_RECOVERY.md",
    "DATABASE_BACKUP_RESTORE.md",
    "FRESH_MIGRATION_RELEASE_GATE.md",
    "PLAYWRIGHT_E2E.md",
    "DB_QUERY_PROFILING.md",
    "RASTER_MEMORY_PROFILING.md",
    "ROAD_HOTPATH_PROFILING.md",
    "PLACEMENT_HOTSPOT_PROFILING.md",
    "TERRITORY_COMPOSITE_PROFILING.md",
)
CLI_MODULE_FLAGS: dict[str, tuple[str, ...]] = {
    "scripts.artifact_gc": ("--execute", "--max-batch", "--max-scan"),
    "benchmarks.db_query_plans": ("--rows", "--output"),
    "benchmarks.raster_memory_reference": (
        "--width", "--height", "--tile-size", "--gdal-cache-mb", "--output",
    ),
    "benchmarks.road_hotpath_profile": (
        "--grid-sizes", "--repetitions", "--snap-queries", "--path-queries", "--output",
    ),
    "benchmarks.placement_hotpath_profile": (
        "--building-counts", "--infrastructure-scales", "--repetitions", "--output",
    ),
    "benchmarks.territory_composite_profile": ("--area-km2", "--output"),
    "worker.recover_interrupted_generation": (
        "--run-id", "--stale-minutes", "--confirm-workers-stopped", "--execute",
    ),
}
PRODUCTION_SERVICES = (
    "api", "migrate", "dispatcher", "frontend",
    "worker-ingest", "worker-generation", "worker-analysis",
    "worker-export", "worker-legacy",
)
LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


def broken_relative_links(document: Path) -> tuple[str, ...]:
    """Check local Markdown destinations, excluding URL fragments."""
    errors: list[str] = []
    for href in LINK_RE.findall(document.read_text(encoding="utf-8")):
        if href.startswith(("http://", "https://", "mailto:", "#")):
            continue
        target = unquote(href.split("#", 1)[0])
        if target and (
            Path(target).is_absolute() or not (document.parent / target).is_file()
        ):
            errors.append(f"{document.name}: missing local link {href}")
    return tuple(errors)


def validate_operations_docs(root: Path = ROOT) -> tuple[str, ...]:
    """Return drift violations without running any documented command."""
    document = root / RUNBOOK
    if not document.is_file():
        return (f"missing {RUNBOOK}",)
    content = document.read_text(encoding="utf-8")
    errors = list(broken_relative_links(document))

    for name in OWNER_DOCS:
        if f"]({name})" not in content:
            errors.append(f"missing canonical owner link: {name}")

    for module, flags in CLI_MODULE_FLAGS.items():
        if f"python -m {module}" not in content:
            errors.append(f"missing CLI example: {module}")
        source = root / (module.replace(".", "/") + ".py")
        if not source.is_file():
            errors.append(f"missing executable module: {module}")
            continue
        source_text = source.read_text(encoding="utf-8")
        for flag in flags:
            if flag not in content or f'"{flag}"' not in source_text:
                errors.append(f"CLI flag mismatch: {module} {flag}")

    compose = root / "compose.production.yml"
    if not compose.is_file():
        errors.append("missing production Compose contract")
    else:
        compose_content = compose.read_text(encoding="utf-8")
        for name in PRODUCTION_SERVICES:
            if f"\n  {name}:\n" not in compose_content:
                errors.append(f"missing production service: {name}")

    for queue in (*REDIS_QUEUES.values(), LEGACY_QUEUE):
        if queue not in content:
            errors.append(f"missing Redis queue: {queue}")

    for entry in ("README.md", "docs/PRODUCTION_COMPOSE.md"):
        path = root / entry
        if not path.is_file() or "OPERATIONS_RUNBOOK.md" not in path.read_text(
            encoding="utf-8"
        ):
            errors.append(f"missing runbook entry point: {entry}")
    return tuple(errors)


def main() -> None:
    errors = validate_operations_docs()
    if errors:
        for error in errors:
            print(f"operations-docs: {error}", file=sys.stderr)
        raise SystemExit(1)
    print("operations-docs: validated links, CLI flags, Redis queues and services")


if __name__ == "__main__":
    main()
