"""Create-only binary uploads and vault destination boundaries."""

from __future__ import annotations

import pytest

from validation.pytest_scenarios.application import Application
from validation.pytest_scenarios.support.vault_inputs import upload

pytestmark = pytest.mark.scenario


async def test_explorer_upload_preserves_bytes_and_enforces_boundaries(
    app: Application,
) -> None:
    vault = app.vault("VaultExplorerUploadVault")
    await app.start()
    payload = b"%PDF-1.7\n\x00binary-upload\n%%EOF"
    path = "AssistantMD/Import/source.pdf"
    assert upload(app, vault.name, path, payload).status_code == 200
    assert (vault / path).read_bytes() == payload
    activity = app.request(f"/api/vaults/{vault.name}/activity").json()
    groups = [
        group
        for group in activity["groups"]
        if group.get("activity_label") == f"Upload {path}"
    ]
    assert len(groups) == 1
    assert any(
        m.get("path") == path
        and m.get("operation") == "write"
        and m.get("after_exists") is True
        for m in groups[0]["mutations"]
    )
    assert upload(app, vault.name, path, b"replacement").status_code == 409
    assert (vault / path).read_bytes() == payload

    assert (
        app.request(
            "/api/system/settings/general/vault_upload_max_mb_per_file",
            "PUT",
            {"value": "1"},
        ).status_code
        == 200
    )
    assert (
        upload(app, vault.name, "oversized.pdf", b"x" * (1024 * 1024 + 1)).status_code
        == 413
    )
    assert not (vault / "oversized.pdf").exists()
    for unsafe in (
        "../outside.pdf",
        "/absolute.pdf",
        ".",
        "folder/./dot.pdf",
        r"C:\outside.pdf",
        "control\x00.pdf",
    ):
        assert upload(app, vault.name, unsafe, payload).status_code == 400, repr(unsafe)
    assert not (vault.parent / "outside.pdf").exists()
    outside = vault.parent / "upload-symlink-target"
    outside.mkdir()
    (vault / "escape-link").symlink_to(outside, target_is_directory=True)
    assert (
        upload(app, vault.name, "escape-link/escaped.pdf", payload).status_code == 400
    )
    assert not (outside / "escaped.pdf").exists()
    assert (
        upload(app, vault.name, "extra-part.pdf", payload, extra_part=True).status_code
        == 400
    )
    assert not (vault / "extra-part.pdf").exists()
    assert (
        upload(
            app, vault.name, "safe-name.pdf", payload, filename="../../server.py"
        ).status_code
        == 200
    )
    assert (vault / "safe-name.pdf").read_bytes() == payload
    assert not (vault.parent / "server.py").exists()
    assert (
        app.request(
            "/api/system/settings/general/vault_upload_max_mb_per_file",
            "PUT",
            {"value": "0"},
        ).status_code
        == 200
    )
    assert upload(app, vault.name, "disabled.pdf", payload).status_code == 403
    assert not (vault / "disabled.pdf").exists()
