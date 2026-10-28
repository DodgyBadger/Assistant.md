"""Real application behavior staged for the pytest migration."""

from __future__ import annotations

import pytest

from validation.pytest_scenarios.application import Application

pytestmark = pytest.mark.scenario


async def test_extensionless_file_edits(app: Application) -> None:
    from typing import Any

    from pydantic_ai.messages import ToolReturn

    from core.identity import LOCAL_USER_AUTHORITY, use_execution_authority
    from core.tools.file_write import FileWrite

    vault = app.vault("FileOpsExtensionlessTextMutationsVault")
    app.write(vault, "notes/edit-target.md", "first\nreplace me\nlast\n")
    app.write(vault, "notes/no-extension", "alpha\n")
    app.write(vault, "notes/crlf-target.md", "first\r\nreplace me\r\nlast\r\n")
    await app.start()
    tool = FileWrite.get_tool(str(vault))

    def file_write(**arguments: Any) -> ToolReturn:
        checkpoint = app.events.checkpoint()
        with use_execution_authority(LOCAL_USER_AUTHORITY):
            result = tool.function(**arguments)
        app.evidence.append(
            "tool-results.jsonl",
            {
                "tool": "file_write",
                "arguments": arguments,
                "metadata": result.metadata,
                "result": result.return_value,
            },
        )
        app.events.require(
            "tool_invoked",
            after=checkpoint,
            tool="file_write",
            operation=arguments["operation"],
        )
        return result

    edited = file_write(
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
    crlf_edit = file_write(
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
    replaced = file_write(
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
    truncated = file_write(
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
    fallback = file_write(
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
