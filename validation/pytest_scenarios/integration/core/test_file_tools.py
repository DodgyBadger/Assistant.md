"""Migrated existing file behavior through real production boundaries."""

from __future__ import annotations

import pytest

from validation.pytest_scenarios.application import Application
from validation.pytest_scenarios.support.file_tools import (
    BoundFileTools,
    copy_image_fixture,
)

pytestmark = pytest.mark.scenario


async def test_split_file_tool_contract(app: Application) -> None:
    vault = app.vault("FileOpsUnifiedToolVault")
    app.write(vault, "notes/source.md", "one\ntwo\nthree\nfour\n")
    app.write(vault, "notes/edit.md", "Alpha beta alpha\n")
    app.write(
        vault,
        "notes/frontmatter.md",
        "---\ntitle: Example\ntags:\n  - test\n---\n\nBody\n",
    )
    app.write(vault, "notes/lines.md", "same\ntarget\nsame\n")
    app.write(vault, "archive/existing.md", "Existing destination\n")
    copy_image_fixture(vault)

    await app.start()

    tools = BoundFileTools(app, vault)
    assert tools.names == ["file_read", "file_write"]
    write_properties = tools.write_schema["properties"]
    assert (
        "operations" not in write_properties
    ), "file_write should expose one mutation per tool call"

    read_range = await tools.call(
        "file_read",
        operation="read",
        path="notes/source.md",
        start_line=2,
        line_count=2,
    )
    assert read_range.return_value == "two\nthree"
    assert read_range.metadata["tool_name"] == "file_read"
    assert read_range.metadata["operation"] == "read"
    assert read_range.metadata["start_line"] == 2
    assert read_range.metadata["lines_returned"] == 2

    listed = await tools.call("file_read", operation="list", path="notes")
    assert listed.metadata["status"] == "completed"
    assert "notes/source.md" in listed.metadata["files"]

    directory_read = await tools.call("file_read", operation="read", path="notes")
    assert directory_read.metadata["operation"] == "list"
    assert "notes/source.md" in directory_read.metadata["files"]

    searched = await tools.call(
        "file_read", operation="search", path="notes", search_term="two"
    )
    assert searched.metadata["status"] == "completed"
    assert searched.metadata["match_count"] >= 1

    glob_search = await tools.call(
        "file_read", operation="search", path="notes/*.md", search_term="two"
    )
    assert glob_search.metadata["status"] == "completed"
    assert any(
        match.startswith("notes/source.md:")
        for match in glob_search.metadata["matches"]
    )

    frontmatter = await tools.call(
        "file_read",
        operation="frontmatter",
        path="notes/frontmatter.md",
        keys="title,tags",
    )
    assert frontmatter.metadata["status"] == "completed"
    assert frontmatter.metadata["file_count"] == 1
    assert frontmatter.metadata["items"] == [
        {
            "path": "notes/frontmatter.md",
            "frontmatter": {"title": "Example", "tags": ["test"]},
        }
    ]

    image = await tools.call(
        "file_read", operation="read", path="images/test_image.jpg"
    )
    assert image.metadata["status"] == "completed"
    assert image.metadata["media_mode"] == "image"
    assert isinstance(image.return_value, list)
    assert len(image.return_value) == 2

    created = await tools.call(
        "file_write", operation="write", path="notes/new.md", content="Created\n"
    )
    assert created.metadata["status"] == "completed"
    assert (vault / "notes/new.md").read_text(encoding="utf-8") == "Created\n"

    duplicate = await tools.call(
        "file_write", operation="write", path="notes/new.md", content="No overwrite\n"
    )
    assert duplicate.metadata["status"] == "already_exists"
    assert duplicate.metadata["error_type"] == "file_exists"
    assert (vault / "notes/new.md").read_text(encoding="utf-8") == "Created\n"

    blank_path = await tools.call(
        "file_write", operation="write", path="  ", content="No path\n", overwrite=True
    )
    assert blank_path.metadata["status"] == "error"
    assert blank_path.metadata["error_type"] == "invalid_path"

    overwritten = await tools.call(
        "file_write",
        operation="write",
        path="notes/new.md",
        content="Overwritten\n",
        overwrite=True,
    )
    assert overwritten.metadata["status"] == "completed"
    assert overwritten.metadata["overwrote"] is True
    assert (vault / "notes/new.md").read_text(encoding="utf-8") == "Overwritten\n"

    appended = await tools.call(
        "file_write", operation="append", path="notes/new.md", content="Appended\n"
    )
    assert appended.metadata["status"] == "completed"
    assert (vault / "notes/new.md").read_text(
        encoding="utf-8"
    ) == "Overwritten\nAppended\n"

    replaced = await tools.call(
        "file_write",
        operation="replace_text",
        path="notes/edit.md",
        old_text="Alpha",
        new_text="Gamma",
        count=1,
    )
    assert replaced.metadata["status"] == "completed"
    assert replaced.metadata["replacement_count"] == 1
    assert (vault / "notes/edit.md").read_text(encoding="utf-8") == "Gamma beta alpha\n"

    edited_line = await tools.call(
        "file_write",
        operation="edit_line",
        path="notes/lines.md",
        line_number=3,
        old_text="same",
        new_text="changed",
    )
    assert edited_line.metadata["status"] == "completed"
    assert edited_line.metadata["line_number"] == 3
    assert (vault / "notes/lines.md").read_text(
        encoding="utf-8"
    ) == "same\ntarget\nchanged\n"

    moved = await tools.call(
        "file_write",
        operation="move",
        path="notes/new.md",
        destination="archive/new.md",
    )
    assert moved.metadata["status"] == "completed"
    assert not (vault / "notes/new.md").exists()
    assert (vault / "archive/new.md").exists()

    moved_overwrite = await tools.call(
        "file_write",
        operation="move",
        path="notes/lines.md",
        destination="archive/existing.md",
        overwrite=True,
    )
    assert moved_overwrite.metadata["status"] == "completed"
    assert moved_overwrite.metadata["overwrote_destination"] is True
    assert not (vault / "notes/lines.md").exists()
    assert (vault / "archive/existing.md").read_text(
        encoding="utf-8"
    ) == "same\ntarget\nchanged\n"

    made_directory = await tools.call("file_write", operation="mkdir", path="empty/sub")
    assert made_directory.metadata["status"] == "completed"
    assert (vault / "empty/sub").is_dir()

    removed_batch = await tools.call("file_write", operation="batch")
    assert removed_batch.metadata["status"] == "error"
    assert removed_batch.metadata["error_type"] == "unknown_operation"
    assert "mkdir" in removed_batch.return_value
    assert "batch" not in removed_batch.return_value.split("Available:", 1)[-1]

    deleted = await tools.call(
        "file_write",
        operation="delete",
        path="archive/new.md",
        confirm_path="archive/new.md",
    )
    assert deleted.metadata["status"] == "completed"
    assert not (vault / "archive/new.md").exists()
