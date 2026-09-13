"""Probe a pinned skill-coupled MCP provider through the advanced shell."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from fastmcp import Client

from core.runtime.paths import set_bootstrap_roots  # noqa: E402

_BOOTSTRAP_ROOT = Path(tempfile.gettempdir()) / "assistantmd-lsp-skill-probe"
set_bootstrap_roots(_BOOTSTRAP_ROOT / "data", _BOOTSTRAP_ROOT / "system")

from core.tools.advanced_shell import ShellTransportConfig  # noqa: E402
from validation.scenarios.experiments.advanced_shell_stdio_mcp_probe import (  # noqa: E402
    _transport,
)

LOG_PATH = Path("scripts/advanced_shell_lsp_skill_probe.latest.log")
PROVIDER_COMMIT = "86b05985943a7396f88d3868b72556465437bc96"
PROVIDER_VERSION = "1.1.20"
SKILL_SHA256 = "1408ed7dc70be64ae0b2399027b59e1ba74ee93a88f29d7e5571e071cbc63632"
PROVIDER_ENTRYPOINT = (
    "/home/advanced-shell/experiments/lsp-mcp-server-86b0598/dist/index.js"
)
WORKSPACE = "/workspace/lsp-mcp-probe-86b0598"
SOURCE_FILE = f"{WORKSPACE}/src/index.ts"


def _result_summary(result: Any) -> dict[str, Any]:
    return {
        "is_error": result.is_error,
        "content_prefix": str(result.content)[:1000],
    }


async def run_probe() -> dict[str, Any]:
    """Exercise tool use that depends on a subordinate language server."""
    config = ShellTransportConfig.from_environment()
    transport = _transport(
        config,
        "/usr/local/bin/node",
        [PROVIDER_ENTRYPOINT],
        cwd=WORKSPACE,
        env={"LSP_LOG_LEVEL": "warn"},
    )
    async with Client(transport, init_timeout=60.0, timeout=60.0) as client:
        tools = await client.list_tools()
        tools_by_name = {tool.name: tool for tool in tools}
        expected_tools = {
            "lsp_document_symbols",
            "lsp_hover",
            "lsp_server_status",
        }
        assert expected_tools <= tools_by_name.keys()

        initial_status = await client.call_tool("lsp_server_status", {})
        document_symbols = await client.call_tool(
            "lsp_document_symbols",
            {"file_path": SOURCE_FILE},
        )
        hover = await client.call_tool(
            "lsp_hover",
            {"file_path": SOURCE_FILE, "line": 28, "column": 10},
        )
        final_status = await client.call_tool("lsp_server_status", {})

        assert not document_symbols.is_error
        assert not hover.is_error
        catalog_payload = json.dumps(
            [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.inputSchema,
                }
                for tool in sorted(tools, key=lambda item: item.name)
            ],
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        return {
            "status": "passed",
            "provider_commit": PROVIDER_COMMIT,
            "provider_version": PROVIDER_VERSION,
            "skill_sha256": SKILL_SHA256,
            "workspace": WORKSPACE,
            "tool_count": len(tools),
            "catalog_sha256": hashlib.sha256(catalog_payload).hexdigest(),
            "tool_names": sorted(tools_by_name),
            "tool_descriptions_present": all(bool(tool.description) for tool in tools),
            "initial_server_status": _result_summary(initial_status),
            "document_symbols": _result_summary(document_symbols),
            "hover": _result_summary(hover),
            "final_server_status": _result_summary(final_status),
            "candidate_connection": {
                "display_name": "LSP code intelligence",
                "executable": "/usr/local/bin/node",
                "args": [PROVIDER_ENTRYPOINT],
                "cwd": WORKSPACE,
                "env": {"LSP_LOG_LEVEL": "warn"},
            },
            "skill_comparison": {
                "provider_skill_is_required_to_launch": False,
                "provider_skill_uses_unprefixed_tool_names": True,
                "assistantmd_model_tools_are_connection_slug_prefixed": True,
                "existing_skill_dependency_metadata_is_enforced": False,
            },
        }


def main() -> None:
    report = asyncio.run(run_probe())
    rendered = json.dumps(report, indent=2, sort_keys=True)
    LOG_PATH.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    print(f"wrote {LOG_PATH}")


if __name__ == "__main__":
    main()
