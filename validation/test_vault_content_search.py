"""Focused tests for structured bounded vault content search."""

from pathlib import Path

import pytest

from core.vault_state.search import VaultContentSearchError, search_vault_content


def test_content_search_is_literal_structured_and_scope_bounded(tmp_path: Path) -> None:
    vault = tmp_path / "Vault"
    scoped = vault / "Notes"
    scoped.mkdir(parents=True)
    (scoped / "name:with:colons.md").write_text(
        "First line\nLiteral [query] appears here\n", encoding="utf-8"
    )
    (vault / "outside.md").write_text("Literal [query] outside\n", encoding="utf-8")
    (scoped / ".hidden.md").write_text("Literal [query] hidden\n", encoding="utf-8")

    result = search_vault_content(
        vault_path=vault,
        path="Notes",
        query="[QUERY]",
    )

    assert result.truncated is False
    assert len(result.matches) == 1
    assert result.matches[0].path == "Notes/name:with:colons.md"
    assert result.matches[0].line == 2
    assert result.matches[0].column == 9
    assert result.matches[0].snippet == "Literal [query] appears here"


def test_content_search_enforces_a_global_result_limit(tmp_path: Path) -> None:
    vault = tmp_path / "Vault"
    vault.mkdir()
    (vault / "many.md").write_text("\n".join(["needle"] * 10), encoding="utf-8")

    result = search_vault_content(vault_path=vault, query="needle", limit=3)

    assert len(result.matches) == 3
    assert result.truncated is True


def test_content_search_rejects_a_scope_outside_the_vault(tmp_path: Path) -> None:
    vault = tmp_path / "Vault"
    vault.mkdir()

    with pytest.raises(VaultContentSearchError, match="safe vault-relative") as exc:
        search_vault_content(vault_path=vault, path="../outside", query="needle")

    assert exc.value.code == "invalid_scope"
