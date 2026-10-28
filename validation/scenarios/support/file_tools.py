"""Production tool invocation and retained file-operation evidence."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:

    from core.authoring.contracts import ScriptToolResult
    from core.vault_state.file_operations import VaultFileOperationResult
    from validation.scenarios.application import Application


class BoundFileTools:
    def __init__(self, app: Application, vault: Path) -> None:
        from core.authoring.shared.tool_binding import resolve_tool_binding
        from core.identity import LOCAL_USER_AUTHORITY, use_execution_authority

        self.app = app
        self.vault = vault
        with use_execution_authority(LOCAL_USER_AUTHORITY):
            self.binding = resolve_tool_binding(
                ["file_read", "file_write"], vault_path=str(vault)
            )

    @property
    def names(self) -> list[str]:
        return self.binding.tool_names()

    @property
    def write_schema(self) -> dict[str, Any]:
        return self.binding.tool_functions[1].function_schema.json_schema

    async def call(self, tool_name: str, **arguments: Any) -> ScriptToolResult:
        from core.authoring.helpers.runtime_common import (
            invoke_bound_tool,
            normalize_tool_result,
        )
        from core.identity import LOCAL_USER_AUTHORITY, use_execution_authority
        from core.runtime.buffers import BufferStore

        tool = self.binding.tool_functions[self.names.index(tool_name)]
        with use_execution_authority(LOCAL_USER_AUTHORITY):
            result = await invoke_bound_tool(
                tool,
                tool_name=tool_name,
                arguments=arguments,
                run_buffers=BufferStore(),
                session_buffers=BufferStore(),
                session_id="file_ops_unified_tool",
                vault_name=self.vault.name,
            )
        normalized = normalize_tool_result(
            tool_name, result, vault_path=str(self.vault)
        )
        self.app.evidence.append(
            "tool-results.jsonl",
            {
                "tool": tool_name,
                "arguments": arguments,
                "metadata": normalized.metadata,
                # Images can carry provider objects; retain their structural shape.
                "result": (
                    normalized.return_value
                    if isinstance(normalized.return_value, str)
                    else {
                        "type": type(normalized.return_value).__name__,
                        "count": len(normalized.return_value),
                    }
                ),
            },
        )
        return normalized


def copy_image_fixture(vault: Path) -> None:
    destination = vault / "images" / "test_image.jpg"
    destination.parent.mkdir(parents=True, exist_ok=True)
    source = Path(__file__).resolve().parents[2] / "fixtures/files/test_image.jpg"
    shutil.copyfile(source, destination)


def record_operation(
    app: Application, result: VaultFileOperationResult
) -> VaultFileOperationResult:
    app.evidence.append(
        "file-operations.jsonl",
        {"metadata": result.metadata, "result": result.return_value},
    )
    return result
