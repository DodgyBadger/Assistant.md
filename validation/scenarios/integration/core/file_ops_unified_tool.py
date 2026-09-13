"""Validate split file_read and file_write tool contracts."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from core.authoring.helpers.runtime_common import (
    invoke_bound_tool,
    normalize_tool_result,
)
from core.authoring.shared.tool_binding import resolve_tool_binding
from validation.core.base_scenario import BaseScenario, with_local_user_authority


class FileOpsUnifiedToolScenario(BaseScenario):
    """Validate file_read/file_write expose the v0.7.0 command-style contract."""

    @with_local_user_authority
    async def test_scenario(self) -> None:
        vault = self.create_vault("FileOpsUnifiedToolVault")
        self.create_file(vault, "notes/source.md", "one\ntwo\nthree\nfour\n")
        self.create_file(vault, "notes/edit.md", "Alpha beta alpha\n")
        self.create_file(
            vault,
            "notes/frontmatter.md",
            "---\ntitle: Example\ntags:\n  - test\n---\n\nBody\n",
        )
        self.create_file(vault, "notes/lines.md", "same\ntarget\nsame\n")
        self.create_file(vault, "archive/existing.md", "Existing destination\n")
        self.copy_files("validation/templates/files/test_image.jpg", vault, "images")

        await self.start_system()
        try:
            binding = resolve_tool_binding(
                ["file_read", "file_write"], vault_path=str(vault)
            )
            assert binding.tool_names() == ["file_read", "file_write"]
            read_tool = binding.tool_functions[0]
            write_tool = binding.tool_functions[1]
            write_properties = write_tool.function_schema.json_schema["properties"]
            assert (
                "operations" not in write_properties
            ), "file_write should expose one mutation per tool call"

            read_range = await self._call(
                read_tool,
                vault,
                "file_read",
                {
                    "operation": "read",
                    "path": "notes/source.md",
                    "start_line": 2,
                    "line_count": 2,
                },
            )
            assert read_range.return_value == "two\nthree"
            assert read_range.metadata["tool_name"] == "file_read"
            assert read_range.metadata["operation"] == "read"
            assert read_range.metadata["start_line"] == 2
            assert read_range.metadata["lines_returned"] == 2

            listed = await self._call(
                read_tool,
                vault,
                "file_read",
                {
                    "operation": "list",
                    "path": "notes",
                },
            )
            assert listed.metadata["status"] == "completed"
            assert "notes/source.md" in listed.metadata["files"]

            directory_read = await self._call(
                read_tool,
                vault,
                "file_read",
                {
                    "operation": "read",
                    "path": "notes",
                },
            )
            assert directory_read.metadata["operation"] == "list"
            assert "notes/source.md" in directory_read.metadata["files"]

            searched = await self._call(
                read_tool,
                vault,
                "file_read",
                {
                    "operation": "search",
                    "path": "notes",
                    "search_term": "two",
                },
            )
            assert searched.metadata["status"] == "completed"
            assert searched.metadata["match_count"] >= 1

            glob_search = await self._call(
                read_tool,
                vault,
                "file_read",
                {
                    "operation": "search",
                    "path": "notes/*.md",
                    "search_term": "two",
                },
            )
            assert glob_search.metadata["status"] == "completed"
            assert any(
                match.startswith("notes/source.md:")
                for match in glob_search.metadata["matches"]
            )

            frontmatter = await self._call(
                read_tool,
                vault,
                "file_read",
                {
                    "operation": "frontmatter",
                    "path": "notes/frontmatter.md",
                    "keys": "title,tags",
                },
            )
            assert frontmatter.metadata["status"] == "completed"
            assert frontmatter.metadata["file_count"] == 1
            assert frontmatter.metadata["items"] == [
                {
                    "path": "notes/frontmatter.md",
                    "frontmatter": {"title": "Example", "tags": ["test"]},
                }
            ]

            image = await self._call(
                read_tool,
                vault,
                "file_read",
                {
                    "operation": "read",
                    "path": "images/test_image.jpg",
                },
            )
            assert image.metadata["status"] == "completed"
            assert image.metadata["media_mode"] == "image"
            assert isinstance(image.return_value, list)
            assert len(image.return_value) == 2

            created = await self._call(
                write_tool,
                vault,
                "file_write",
                {
                    "operation": "write",
                    "path": "notes/new.md",
                    "content": "Created\n",
                },
            )
            assert created.metadata["status"] == "completed"
            assert (vault / "notes/new.md").read_text(encoding="utf-8") == "Created\n"

            duplicate = await self._call(
                write_tool,
                vault,
                "file_write",
                {
                    "operation": "write",
                    "path": "notes/new.md",
                    "content": "No overwrite\n",
                },
            )
            assert duplicate.metadata["status"] == "already_exists"
            assert duplicate.metadata["error_type"] == "file_exists"
            assert (vault / "notes/new.md").read_text(encoding="utf-8") == "Created\n"

            blank_path = await self._call(
                write_tool,
                vault,
                "file_write",
                {
                    "operation": "write",
                    "path": "  ",
                    "content": "No path\n",
                    "overwrite": True,
                },
            )
            assert blank_path.metadata["status"] == "error"
            assert blank_path.metadata["error_type"] == "invalid_path"

            overwritten = await self._call(
                write_tool,
                vault,
                "file_write",
                {
                    "operation": "write",
                    "path": "notes/new.md",
                    "content": "Overwritten\n",
                    "overwrite": True,
                },
            )
            assert overwritten.metadata["status"] == "completed"
            assert overwritten.metadata["overwrote"] is True
            assert (vault / "notes/new.md").read_text(
                encoding="utf-8"
            ) == "Overwritten\n"

            appended = await self._call(
                write_tool,
                vault,
                "file_write",
                {
                    "operation": "append",
                    "path": "notes/new.md",
                    "content": "Appended\n",
                },
            )
            assert appended.metadata["status"] == "completed"
            assert (vault / "notes/new.md").read_text(
                encoding="utf-8"
            ) == "Overwritten\nAppended\n"

            replaced = await self._call(
                write_tool,
                vault,
                "file_write",
                {
                    "operation": "replace_text",
                    "path": "notes/edit.md",
                    "old_text": "Alpha",
                    "new_text": "Gamma",
                    "count": 1,
                },
            )
            assert replaced.metadata["status"] == "completed"
            assert replaced.metadata["replacement_count"] == 1
            assert (vault / "notes/edit.md").read_text(
                encoding="utf-8"
            ) == "Gamma beta alpha\n"

            edited_line = await self._call(
                write_tool,
                vault,
                "file_write",
                {
                    "operation": "edit_line",
                    "path": "notes/lines.md",
                    "line_number": 3,
                    "old_text": "same",
                    "new_text": "changed",
                },
            )
            assert edited_line.metadata["status"] == "completed"
            assert edited_line.metadata["line_number"] == 3
            assert (vault / "notes/lines.md").read_text(encoding="utf-8") == (
                "same\ntarget\nchanged\n"
            )

            moved = await self._call(
                write_tool,
                vault,
                "file_write",
                {
                    "operation": "move",
                    "path": "notes/new.md",
                    "destination": "archive/new.md",
                },
            )
            assert moved.metadata["status"] == "completed"
            assert not (vault / "notes/new.md").exists()
            assert (vault / "archive/new.md").exists()

            moved_overwrite = await self._call(
                write_tool,
                vault,
                "file_write",
                {
                    "operation": "move",
                    "path": "notes/lines.md",
                    "destination": "archive/existing.md",
                    "overwrite": True,
                },
            )
            assert moved_overwrite.metadata["status"] == "completed"
            assert moved_overwrite.metadata["overwrote_destination"] is True
            assert not (vault / "notes/lines.md").exists()
            assert (vault / "archive/existing.md").read_text(encoding="utf-8") == (
                "same\ntarget\nchanged\n"
            )

            made_directory = await self._call(
                write_tool,
                vault,
                "file_write",
                {
                    "operation": "mkdir",
                    "path": "empty/sub",
                },
            )
            assert made_directory.metadata["status"] == "completed"
            assert (vault / "empty/sub").is_dir()

            removed_batch = await self._call(
                write_tool,
                vault,
                "file_write",
                {
                    "operation": "batch",
                },
            )
            assert removed_batch.metadata["status"] == "error"
            assert removed_batch.metadata["error_type"] == "unknown_operation"
            assert "mkdir" in removed_batch.return_value
            assert "batch" not in removed_batch.return_value.split("Available:", 1)[-1]

            deleted = await self._call(
                write_tool,
                vault,
                "file_write",
                {
                    "operation": "delete",
                    "path": "archive/new.md",
                    "confirm_path": "archive/new.md",
                },
            )
            assert deleted.metadata["status"] == "completed"
            assert not (vault / "archive/new.md").exists()
        finally:
            await self.stop_system()
            self.teardown_scenario()

    async def _call(self, tool, vault: Path, tool_name: str, arguments: dict):
        result = await invoke_bound_tool(
            tool,
            tool_name=tool_name,
            arguments=arguments,
            run_buffers={},
            session_buffers={},
            session_id="file_ops_unified_tool",
            vault_name=vault.name,
        )
        return normalize_tool_result(tool_name, result, vault_path=str(vault))
