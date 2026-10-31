# Deferred reference only; see validation/registry/pytest-gaps.json.
# Original complete source is available in Git before the 2026-10-31 cleanup.
"""Focused tests for collision-consistent, compensated import storage."""

from pathlib import Path

import pytest

from core.ingestion.models import RenderOptions
from core.ingestion.storage import default_storage
from core.utils.hash import hash_file_bytes
from core.vault_state.file_mutations import VaultMutationRejected


def _restore_exact_file_states(*, vault_path: Path, states: tuple) -> tuple:
    for state in states:
        target = Path(vault_path) / state.path
        if (
            not target.exists()
            or hash_file_bytes(target, length=None) != state.expected_sha256
        ):
            raise VaultMutationRejected("file_conflict", "current file state changed")
        target.unlink()
    return ()


def test_storage_compensates_prior_outputs_when_a_later_write_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "Vault"
    vault.mkdir()
    monkeypatch.setattr("core.ingestion.storage.get_data_root", lambda: str(tmp_path))

    def write_text(*, vault_path: Path, path: str, content: str, **_kwargs) -> None:
        target = Path(vault_path) / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def write_bytes_then_raise(
        *, vault_path: Path, path: str, content: bytes, **_kwargs
    ) -> None:
        target = Path(vault_path) / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        raise RuntimeError("injected post-write failure")

    monkeypatch.setattr("core.ingestion.storage.write_vault_file", write_text)
    monkeypatch.setattr(
        "core.ingestion.storage.write_vault_file_bytes",
        write_bytes_then_raise,
    )
    monkeypatch.setattr(
        "core.ingestion.storage.restore_file_states_atomically",
        _restore_exact_file_states,
    )

    with pytest.raises(RuntimeError, match="injected post-write failure"):
        default_storage(
            [
                {"path": "Imported/report.md", "content": "markdown"},
                {"path": "Imported/assets/report/image.png", "content_bytes": b"x"},
            ],
            RenderOptions(vault="Vault"),
        )

    assert not (vault / "Imported/report.md").exists()
    assert not (vault / "Imported/assets/report/image.png").exists()
