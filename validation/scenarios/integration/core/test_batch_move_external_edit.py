"""Batch compensation preserves a newer external edit after the real move."""

from hashlib import sha256

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.batch_move_external_edit import (
    delay_move_audit_and_edit,
)
from validation.scenarios.support.vault_recovery import query_rows

pytestmark = pytest.mark.scenario


async def test_batch_move_compensation_preserves_external_edit(
    app: Application,
) -> None:
    vault = app.vault("BatchMoveExternalEditVault")
    app.write(vault, "One/a.md", "FIRST_ORIGINAL")
    app.write(vault, "Two/b.md", "SECOND_ORIGINAL")
    (vault / "Archive").mkdir()
    await app.start()
    target = vault / "Archive/b.md"
    with delay_move_audit_and_edit(app, target) as (observed, errors):
        response = await app.request_async(
            f"/api/vaults/{vault.name}/paths/move-batch",
            method="POST",
            data={"sources": ["One/a.md", "Two/b.md"], "destination": "Archive"},
        )
        assert observed.is_set() and not errors
    assert response.status_code >= 400
    assert (vault / "One/a.md").read_text() == "FIRST_ORIGINAL"
    source = vault / "Two/b.md"
    assert source.read_text() == "NEWER_EXTERNAL_EDIT"
    assert not target.exists()
    assert query_rows(
        app,
        "SELECT content_hash FROM vault_files WHERE path=? AND deleted_at IS NULL",
        ("Two/b.md",),
    ) == [{"content_hash": sha256(b"NEWER_EXTERNAL_EDIT").hexdigest()}]
    assert not (vault / "Archive/a.md").exists()
