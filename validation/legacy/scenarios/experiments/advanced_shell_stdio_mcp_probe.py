"""Probe structured stdio MCP sessions through the advanced-shell SSH target."""

from __future__ import annotations

import asyncio
import base64
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from core.runtime.paths import set_bootstrap_roots

_BOOTSTRAP_ROOT = Path(tempfile.gettempdir()) / "assistantmd-stdio-mcp-probe"
set_bootstrap_roots(_BOOTSTRAP_ROOT / "data", _BOOTSTRAP_ROOT / "system")

from fastmcp import Client  # noqa: E402
from fastmcp.client.transports import StdioTransport  # noqa: E402

from core.tools.advanced_shell import (  # noqa: E402
    FixedSshShellExecutor,
    ShellTransportConfig,
)

LOG_PATH = Path("scripts/advanced_shell_stdio_mcp_probe.latest.log")
STRUCTURED_STDIO_PREFIX = "assistantmd-stdio-v1:"


def _structured_launch(
    executable: str,
    args: list[str],
    *,
    cwd: str = "/workspace",
    env: dict[str, str] | None = None,
) -> str:
    payload = json.dumps(
        {
            "executable": executable,
            "args": args,
            "cwd": cwd,
            "env": env or {},
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return STRUCTURED_STDIO_PREFIX + base64.urlsafe_b64encode(payload).decode("ascii")


def _transport(
    config: ShellTransportConfig,
    executable: str,
    args: list[str],
    *,
    cwd: str = "/workspace",
    env: dict[str, str] | None = None,
) -> StdioTransport:
    remote_command = _structured_launch(
        executable,
        args,
        cwd=cwd,
        env=env,
    )
    ssh_command = FixedSshShellExecutor(config)._ssh_command(remote_command)
    assert ssh_command[0] == "ssh"
    return StdioTransport(
        command=ssh_command[0],
        args=ssh_command[1:],
        keep_alive=False,
    )


async def _probe_server(
    *,
    config: ShellTransportConfig,
    name: str,
    executable: str,
    args: list[str],
    roots: list[str] | None = None,
    call: tuple[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    transport = _transport(config, executable, args)
    async with Client(
        transport,
        roots=roots,
        init_timeout=60.0,
        timeout=30.0,
    ) as client:
        initialize_result = client.initialize_result
        assert initialize_result is not None
        server_capabilities = initialize_result.capabilities
        tools = await client.list_tools()
        prompts = (
            await client.list_prompts()
            if server_capabilities.prompts is not None
            else []
        )
        resources = (
            await client.list_resources()
            if server_capabilities.resources is not None
            else []
        )
        resource_templates = (
            await client.list_resource_templates()
            if server_capabilities.resources is not None
            else []
        )
        tool_names = sorted(tool.name for tool in tools)
        result_text = ""
        if call is not None:
            tool_name, arguments = call
            assert tool_name in tool_names
            result = await client.call_tool(tool_name, arguments)
            result_text = str(result)[:500]
        capabilities = server_capabilities.model_dump(exclude_none=True)
        return {
            "name": name,
            "protocol_version": initialize_result.protocolVersion,
            "server_instructions_present": bool(initialize_result.instructions),
            "capability_names": sorted(capabilities),
            "tool_count": len(tool_names),
            "tool_names": tool_names,
            "prompt_count": len(prompts),
            "prompt_names": sorted(prompt.name for prompt in prompts),
            "resource_count": len(resources),
            "resource_uris": sorted(str(resource.uri) for resource in resources),
            "resource_template_count": len(resource_templates),
            "resource_template_uris": sorted(
                str(template.uriTemplate) for template in resource_templates
            ),
            "call_result_prefix": result_text,
        }


async def run_probe() -> dict[str, Any]:
    """Run representative Python, filesystem/Roots, and reference providers."""
    config = ShellTransportConfig.from_environment()
    servers = [
        await _probe_server(
            config=config,
            name="fetch",
            executable="/home/advanced-shell/.local/bin/mcp-server-fetch",
            args=[],
            call=("fetch", {"url": "https://example.com", "max_length": 1000}),
        ),
        await _probe_server(
            config=config,
            name="filesystem_with_roots",
            executable="/home/advanced-shell/.local/bin/mcp-server-filesystem",
            args=[],
            roots=["file:///workspace"],
            call=("list_allowed_directories", {}),
        ),
        await _probe_server(
            config=config,
            name="everything",
            executable="/home/advanced-shell/.local/bin/mcp-server-everything",
            args=[],
            call=("echo", {"message": "assistantmd-stdio-probe"}),
        ),
    ]
    return {
        "status": "passed",
        "server_count": len(servers),
        "servers": servers,
    }


def main() -> None:
    report = asyncio.run(run_probe())
    rendered = json.dumps(report, indent=2, sort_keys=True)
    LOG_PATH.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    print(f"wrote {LOG_PATH}")


if __name__ == "__main__":
    main()
