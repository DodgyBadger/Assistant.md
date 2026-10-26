"""Focused tests for structured bounded vault content search."""

from pathlib import Path
from types import SimpleNamespace

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


@pytest.mark.parametrize(
    ("failure", "expected_code"),
    [
        (FileNotFoundError("missing rg"), "ripgrep_not_found"),
        (OSError("cannot start"), "search_failed"),
    ],
)
def test_content_search_reports_stable_startup_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: OSError,
    expected_code: str,
) -> None:
    vault = tmp_path / "Vault"
    vault.mkdir()
    monkeypatch.setattr(
        "core.vault_state.search.subprocess.Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(failure),
    )

    with pytest.raises(VaultContentSearchError) as exc:
        search_vault_content(vault_path=vault, query="needle")

    assert exc.value.code == expected_code


def test_content_search_reports_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "Vault"
    vault.mkdir()
    process = SimpleNamespace(
        stdout=object(),
        returncode=None,
        poll=lambda: None,
        terminate=lambda: setattr(process, "returncode", -15),
        wait=lambda **_kwargs: process.returncode,
        kill=lambda: setattr(process, "returncode", -9),
    )
    monkeypatch.setattr(
        "core.vault_state.search.subprocess.Popen", lambda *_args, **_kwargs: process
    )
    monkeypatch.setattr(
        "core.vault_state.search.select.select", lambda *_args, **_kwargs: ([], [], [])
    )

    with pytest.raises(VaultContentSearchError) as exc:
        search_vault_content(
            vault_path=vault,
            query="needle",
            timeout_seconds=0.1,
        )

    assert exc.value.code == "timeout"
