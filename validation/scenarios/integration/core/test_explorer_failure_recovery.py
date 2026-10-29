"""Real mutation-audit and partial-write failures preserve Explorer state."""

import errno
import sqlite3

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.explorer_failure_recovery import (
    limit_file_writes,
    reject_second_move,
)

pytestmark = pytest.mark.scenario


@pytest.mark.parametrize("kind", ["file", "directory"])
async def test_batch_move_compensates_real_audit_failure(
    app: Application, kind: str
) -> None:
    vault = app.vault("ExplorerCompensationVault")
    if kind == "file":
        app.write(vault, "One/a.md", "A")
        app.write(vault, "Two/b.md", "B")
        sources = ["One/a.md", "Two/b.md"]
        expected = {"One/a.md": "A", "Two/b.md": "B"}
        targets = ["Archive/a.md", "Archive/b.md"]
    else:
        app.write(vault, "One/a.md", "A")
        app.write(vault, "Two/b.md", "B")
        sources = ["One", "Two"]
        expected = {"One/a.md": "A", "Two/b.md": "B"}
        targets = ["Archive/One", "Archive/Two"]
    (vault / "Archive").mkdir()
    await app.start()
    with reject_second_move(app, sources[1]):
        response = await app.request_async(
            f"/api/vaults/{vault.name}/paths/move-batch",
            method="POST",
            data={"sources": sources, "destination": "Archive"},
        )
    assert response.status_code >= 400
    for path, content in expected.items():
        assert (vault / path).read_text() == content
    assert all(not (vault / target).exists() for target in targets)
    with sqlite3.connect(app.root / "system/vault_state.db") as conn:
        rows = conn.execute(
            "SELECT path, related_path, operation FROM vault_mutations WHERE operation='move' ORDER BY id"
        ).fetchall()
        manifest = conn.execute(
            "SELECT path, deleted_at FROM vault_files WHERE path IN ('One/a.md', 'Two/b.md', 'Archive/a.md', 'Archive/b.md', 'Archive/One/a.md', 'Archive/Two/b.md')"
        ).fetchall()
    assert any(row[0] == sources[0] and row[2] == "move" for row in rows)
    assert any(row[0] == targets[0] and row[2] == "move" for row in rows)
    active = {path for path, deleted in manifest if deleted is None}
    assert set(expected) <= active and not any(
        path.startswith("Archive/") for path in active
    )


async def test_partial_upload_failure_removes_partial_file(app: Application) -> None:
    vault = app.vault("ExplorerUploadPartialVault")
    app.write(vault, "Uploads/existing.bin", "PRESERVE_EXISTING")
    await app.start()
    from api.exceptions import APIException
    from api.services.vault_files import upload_vault_file
    from core.identity import LOCAL_USER_AUTHORITY, use_execution_authority

    with use_execution_authority(LOCAL_USER_AUTHORITY):
        with pytest.raises(APIException) as conflict:
            upload_vault_file(
                vault_name=vault.name,
                path="Uploads/existing.bin",
                content=b"replacement",
            )
        assert conflict.value.status_code == 409
        with limit_file_writes(1024 * 1024):
            with pytest.raises(OSError) as failure:
                upload_vault_file(
                    vault_name=vault.name,
                    path="Uploads/partial.bin",
                    content=b"x" * (2 * 1024 * 1024),
                )
        assert failure.value.errno == errno.EFBIG
    assert not (vault / "Uploads/partial.bin").exists()
    assert (vault / "Uploads/existing.bin").read_text() == "PRESERVE_EXISTING"
