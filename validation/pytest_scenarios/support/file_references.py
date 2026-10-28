"""Controlled vault inputs and durable vault-state observations."""

from __future__ import annotations

import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

from validation.pytest_scenarios.application import Application


def reference_vault(app: Application) -> Path:
    vault = app.vault("VaultFileReferenceApiVault")
    punctuation_path = "Library/! Primary Sources/Lehmann Springs/index.md"
    app.write(vault, punctuation_path, "# Primary sources\n")
    app.write(vault, "Projects/Alpha/README.md", "# Alpha\n\nStart here.\n")
    app.write(vault, "Projects/Alpha/notes.txt", "plain notes\n")
    app.write(vault, "Projects/Alpha/Nested/plan.md", "# Plan\n")
    app.write(vault, "README.md", "# Vault root\n")
    app.write(vault, "root-note.md", "root note\n")
    app.write(vault, "script.py", "print('editable')\n")
    app.write(vault, "revision-restore.md", "first revision\n")
    app.write(vault, "Pagination/a.md", "a\n")
    app.write(vault, "Pagination/b.md", "b\n")
    app.write(vault, "Pagination/c.md", "c\n")
    app.write(vault, "Spanish/lesson.md", "hola\n")
    app.write(vault, "Spanish-vocabulary.md", "hola\n")
    app.write(vault, ".hidden/secret.md", "hidden\n")
    (vault / "binary.docx").write_bytes(b"PK\x03\x04\x00\x00not plain text")
    return vault


def vault_file_events(app: Application) -> list[dict[str, object]]:
    """Read durable creation/hash events; file/revision APIs lack this history."""
    with closing(sqlite3.connect(app.root / "system" / "vault_state.db")) as connection:
        connection.row_factory = sqlite3.Row
        rows = [
            dict(row)
            for row in connection.execute(
                "SELECT event_type, path, content_hash FROM vault_file_events ORDER BY sequence"
            )
        ]
    app.evidence.save("vault-file-events.json", rows)
    return rows


def scan_budget_files(vault: Path, *, count: int) -> Path:
    directory = vault / "ScanLimit"
    directory.mkdir()
    for index in range(count):
        (directory / f"file-{index:05}.md").touch()
    return directory


def remove_scan_budget_files(directory: Path) -> None:
    shutil.rmtree(directory)
