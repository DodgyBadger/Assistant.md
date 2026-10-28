"""Existing file-operation behavior through real application boundaries."""

from __future__ import annotations

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.file_boundaries import list_paths

pytestmark = pytest.mark.scenario


async def test_recursive_root_and_virtual_mount_listing(app: Application) -> None:
    vault = app.vault("FileReadRecursiveListVault")
    app.write(vault, "top.md", "top\n")
    app.write(vault, "projects/alpha/note.md", "alpha\n")
    app.write(vault, "projects/beta/nested/task.txt", "task\n")
    await app.start()

    root_listing = list_paths(app, vault, path="", max_results=200)
    root_files = set(root_listing.metadata.get("files") or [])
    root_directories = set(root_listing.metadata.get("directories") or [])
    assert (
        "projects/alpha/note.md" in root_files
    ), "Recursive root listing should include nested files"
    assert (
        "projects/beta/nested/task.txt" in root_files
    ), "Recursive root listing should include deeply nested files"
    assert {
        "projects",
        "projects/alpha",
        "projects/beta",
        "projects/beta/nested",
    }.issubset(
        root_directories
    ), "Recursive root listing should include nested directories"

    dot_listing = list_paths(app, vault, path=".", max_results=200)
    assert dot_listing.metadata.get("files") == root_listing.metadata.get(
        "files"
    ), "Recursive '.' listing should match recursive root listing"

    docs_listing = list_paths(app, vault, path="__virtual_docs__", max_results=500)
    assert "__virtual_docs__/tools/file_read.md" in set(
        docs_listing.metadata.get("files") or []
    ), "Recursive virtual mount root listing should include nested docs"
