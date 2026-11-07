"""Real session tool calls over isolated persisted discovery inputs."""

from __future__ import annotations

import json
from typing import Any

from validation.support.application import Application


async def call_map_tool(
    app: Application, vault_name: str, **arguments: Any
) -> dict[str, Any]:
    from core.authoring.helpers.runtime_common import invoke_bound_tool
    from core.authoring.shared.tool_binding import resolve_tool_binding
    from core.identity import LOCAL_USER_AUTHORITY, use_execution_authority
    from core.runtime.buffers import BufferStore

    with use_execution_authority(LOCAL_USER_AUTHORITY):
        binding = resolve_tool_binding(
            ["session_ops"], vault_path=str(app.vault(vault_name))
        )
        result = await invoke_bound_tool(
            binding.tool_functions[0],
            tool_name="session_ops",
            arguments=arguments,
            run_buffers=BufferStore(),
            session_buffers=BufferStore(),
            session_id="long",
            vault_name=vault_name,
        )
    app.evidence.append(
        "session-map-tool-results.jsonl",
        {"arguments": arguments, "result": result.return_value},
    )
    return json.loads(result.return_value)


def set_map_setting(app: Application, name: str, value: int) -> None:
    response = app.request(
        f"/api/system/settings/general/{name}", "PUT", {"value": str(value)}
    )
    assert response.status_code == 200, response.text
