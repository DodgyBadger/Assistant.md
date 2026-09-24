"""Focused tests for bounded Vault Explorer filename search."""

from pathlib import Path
from tempfile import TemporaryDirectory

from core.runtime.paths import set_bootstrap_roots

_TEST_ROOT = TemporaryDirectory(prefix="assistantmd-file-reference-search-")
_TEST_ROOT_PATH = Path(_TEST_ROOT.name)
set_bootstrap_roots(_TEST_ROOT_PATH / "data", _TEST_ROOT_PATH / "system")


def test_filename_search_stops_at_the_scan_limit(tmp_path: Path) -> None:
    from api.services.vault_files import _search_vault_file_references

    vault = tmp_path / "Vault"
    vault.mkdir()
    for index in range(5):
        (vault / f"file-{index}.md").write_text("content", encoding="utf-8")

    matches, truncated = _search_vault_file_references(
        vault_root=vault,
        base_dir=vault,
        workspace_path="",
        query="missing",
        limit=100,
        scan_limit=2,
    )

    assert matches == []
    assert truncated is True


def test_filename_search_matches_basename_without_duplicating_path_matches(
    tmp_path: Path,
) -> None:
    from api.services.vault_files import _search_vault_file_references

    vault = tmp_path / "Vault"
    folder = vault / "Spanish"
    folder.mkdir(parents=True)
    (folder / "notes.md").write_text("content", encoding="utf-8")

    matches, truncated = _search_vault_file_references(
        vault_root=vault,
        base_dir=vault,
        workspace_path="",
        query="spanish",
        limit=100,
    )

    assert [match.path for match in matches] == ["Spanish"]
    assert truncated is False
