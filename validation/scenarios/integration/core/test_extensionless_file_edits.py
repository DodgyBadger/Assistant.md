"""Real application behavior staged for the pytest migration."""

from __future__ import annotations

import pytest

from validation.support.application import Application

pytestmark = pytest.mark.scenario


async def test_extensionless_file_edits(app: Application) -> None:
    from typing import Any

    from core.authoring.contracts import ScriptToolResult
    from validation.support.vault.file_tools import BoundFileTools

    vault = app.vault("FileOpsExtensionlessTextMutationsVault")
    app.write(vault, "notes/edit-target.md", "first\nreplace me\nlast\n")
    app.write(vault, "notes/no-extension", "alpha\n")
    app.write(vault, "notes/crlf-target.md", "first\r\nreplace me\r\nlast\r\n")
    await app.start()
    tools = BoundFileTools(app, vault)

    async def file_write(**arguments: Any) -> ScriptToolResult:
        checkpoint = app.events.checkpoint()
        result = await tools.call("file_write", **arguments)
        app.events.require(
            "tool_invoked",
            after=checkpoint,
            tool="file_write",
            operation=arguments["operation"],
        )
        return result

    edited = await file_write(
        operation="edit_line",
        path="notes/edit-target",
        line_number=2,
        old_text="replace me",
        new_text="changed",
    )
    assert (
        edited.metadata.get("status") == "completed"
    ), "Extensionless edit_line should resolve to the existing markdown file"
    assert (
        edited.metadata.get("path") == "notes/edit-target.md"
    ), "edit_line metadata should report the effective markdown path"
    assert (
        edited.metadata.get("requested_path") == "notes/edit-target"
    ), "edit_line metadata should preserve the requested extensionless path"
    assert (vault / "notes" / "edit-target.md").read_text(
        encoding="utf-8"
    ) == "first\nchanged\nlast\n", (
        "edit_line should mutate the markdown file selected by extensionless resolution"
    )
    crlf_edit = await file_write(
        operation="edit_line",
        path="notes/crlf-target.md",
        line_number=2,
        old_text="replace me",
        new_text="changed",
    )
    assert (
        crlf_edit.metadata.get("status") == "completed"
    ), "edit_line should compare CRLF lines without requiring a trailing carriage return"
    assert (
        vault / "notes" / "crlf-target.md"
    ).read_bytes() == b"first\r\nchanged\r\nlast\r\n", (
        "edit_line should preserve CRLF line endings when replacing a line"
    )
    replaced = await file_write(
        operation="replace_text",
        path="notes/edit-target",
        old_text="changed",
        new_text="done",
        count=1,
    )
    assert (
        replaced.metadata.get("path") == "notes/edit-target.md"
    ), "replace_text metadata should report the effective markdown path"
    assert (vault / "notes" / "edit-target.md").read_text(
        encoding="utf-8"
    ) == "first\ndone\nlast\n", "replace_text should mutate the markdown file selected by extensionless resolution"
    truncated = await file_write(
        operation="write", path="notes/edit-target", content="", overwrite=True
    )
    assert (
        truncated.metadata.get("path") == "notes/edit-target.md"
    ), "truncate metadata should report the effective markdown path"
    assert (vault / "notes" / "edit-target.md").read_text(
        encoding="utf-8"
    ) == "", (
        "truncate should clear the markdown file selected by extensionless resolution"
    )
    fallback = await file_write(
        operation="replace_text",
        path="notes/no-extension",
        old_text="alpha",
        new_text="beta",
        count=1,
    )
    assert (
        fallback.metadata.get("path") == "notes/no-extension"
    ), "Extensionless fallback should still allow an existing no-extension target when no .md file exists"
    assert (vault / "notes" / "no-extension").read_text(
        encoding="utf-8"
    ) == "beta\n", "Extensionless fallback should mutate the original target when no markdown sibling exists"
