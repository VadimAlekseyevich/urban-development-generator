import ast
from pathlib import Path

CORE_ROOT = Path(__file__).resolve().parents[2] / "core" / "urban_generator"
FORBIDDEN_CORE_IMPORT_ROOTS = {"fastapi", "sqlalchemy", "redis", "arq", "backend", "worker"}
LEGACY_PIPELINE_SYMBOLS = {"PipelineStage", "GenerationPipeline"}


def _python_files() -> tuple[Path, ...]:
    return tuple(sorted(CORE_ROOT.rglob("*.py")))


def _import_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".", 1)[0])
    return roots


def test_core_does_not_import_application_or_infrastructure_frameworks() -> None:
    violations: list[str] = []
    for path in _python_files():
        forbidden = sorted(_import_roots(path) & FORBIDDEN_CORE_IMPORT_ROOTS)
        if forbidden:
            violations.append(f"{path.relative_to(CORE_ROOT)}: {', '.join(forbidden)}")

    assert violations == []


def test_legacy_second_pipeline_model_does_not_return() -> None:
    violations: list[str] = []
    for path in _python_files():
        text = path.read_text(encoding="utf-8")
        hits = sorted(symbol for symbol in LEGACY_PIPELINE_SYMBOLS if symbol in text)
        if hits:
            violations.append(f"{path.relative_to(CORE_ROOT)}: {', '.join(hits)}")

    assert violations == []


def test_networkx_is_confined_to_the_network_adapter() -> None:
    violations: list[str] = []
    allowed = Path("roads/networkx_backend.py")

    for path in _python_files():
        relative = path.relative_to(CORE_ROOT)
        if relative == allowed:
            continue
        if "networkx" in _import_roots(path):
            violations.append(str(relative))

    assert violations == []
