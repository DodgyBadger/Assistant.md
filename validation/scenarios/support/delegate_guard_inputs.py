"""Real file operations behind public delegate failure-guard callbacks."""

from __future__ import annotations

import asyncio
import json


class GuardInputs:
    def __init__(self, vault):
        from core.tools.file_read import FileRead

        self.tool = FileRead.get_tool(vault_path=str(vault)).function
        self.executed = []
        self.parallel_started = 0
        self.parallel_release = asyncio.Event()

    async def fail(self, args):
        self.executed.append(args)
        # Missing files are benign domain results (status=not_found). An
        # unsupported operation genuinely exercises the production error return.
        result = self.tool(operation="invalid-guard-operation", path="notes/exists.md")
        assert result.metadata["status"] == "error"
        return result

    async def succeed(self, args):
        self.executed.append(args)
        return self.tool(operation="read", path="notes/exists.md")

    async def parse_invalid(self, args):
        json.loads("{invalid configured callback input")

    async def parallel_failure(self, args):
        self.parallel_started += 1
        if self.parallel_started == 2:
            self.parallel_release.set()
        await self.parallel_release.wait()
        return await self.fail(args)
