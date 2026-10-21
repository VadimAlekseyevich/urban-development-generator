"""Regression coverage for the operator-documentation drift checker."""

from pathlib import Path

from scripts.check_operations_docs import broken_relative_links, validate_operations_docs


def test_runbook_matches_cli_services_and_queues() -> None:
    assert validate_operations_docs() == ()


def test_missing_local_document_link_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "RUNBOOK.md"
    (tmp_path / "valid.md").write_text("ok", encoding="utf-8")
    path.write_text(
        "[valid](valid.md) [missing](not-a-file.md) "
        "[external](https://example.com) [section](#intro)",
        encoding="utf-8",
    )
    assert broken_relative_links(path) == (
        "RUNBOOK.md: missing local link not-a-file.md",
    )
