"""Validate browser concurrency, call-budget, and memory admission policy."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from pydantic_ai.messages import ToolReturn

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

import core.tools.browser as browser_module
from core.runtime.resources import CgroupMemoryStatus
from core.tools.browser import BrowserTool
from validation.support.base_scenario import BaseScenario


class BrowserResourcePolicyScenario(BaseScenario):
    """Prove browser calls serialize and reject unsafe admission."""

    async def test_scenario(self) -> None:
        original_browse = BrowserTool.__dict__["_browse"]
        original_concurrency = browser_module.get_browser_max_concurrent_sessions
        original_call_limit = browser_module.get_browser_max_calls_per_turn
        original_headroom = browser_module.get_browser_min_memory_headroom_bytes
        original_memory = browser_module.read_cgroup_memory_status
        BrowserTool._session_semaphore = None
        BrowserTool._session_semaphore_limit = 0
        BrowserTool._active_sessions = 0
        BrowserTool._call_counts.clear()

        active = 0
        max_active = 0

        async def fake_browse(_cls, **_kwargs) -> str:
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0.02)
            active -= 1
            return "content"

        try:
            BrowserTool._browse = classmethod(fake_browse)
            browser_module.get_browser_max_concurrent_sessions = lambda: 1
            browser_module.get_browser_max_calls_per_turn = lambda: 10
            browser_module.get_browser_min_memory_headroom_bytes = lambda: 1
            browser_module.read_cgroup_memory_status = lambda: CgroupMemoryStatus(
                current_bytes=1,
                max_bytes=1024,
            )
            tool = BrowserTool.get_tool()
            await asyncio.gather(
                tool.function(url="data:text/html,one"),
                tool.function(url="data:text/html,two"),
            )
            self.soft_assert_equal(
                max_active,
                1,
                "Process-wide browser semaphore should serialize sessions",
            )

            browser_module.get_browser_min_memory_headroom_bytes = lambda: 512
            browser_module.read_cgroup_memory_status = lambda: CgroupMemoryStatus(
                current_bytes=900,
                max_bytes=1000,
            )
            refused = await tool.function(url="data:text/html,low-memory")
            self.soft_assert(
                isinstance(refused, ToolReturn),
                "Low-memory browser admission should return a structured failure",
            )
            metadata = refused.metadata if isinstance(refused.metadata, dict) else {}
            self.soft_assert_equal(
                metadata.get("status"),
                "failed",
                "Low-memory browser admission should be marked failed",
            )

            BrowserTool._call_counts.clear()
            browser_module.get_browser_max_calls_per_turn = lambda: 2
            BrowserTool._record_scoped_call()
            BrowserTool._record_scoped_call()
            try:
                BrowserTool._record_scoped_call()
            except RuntimeError as exc:
                self.soft_assert(
                    "call limit exceeded" in str(exc).lower(),
                    "Browser-specific call budget should fail clearly",
                )
            else:
                self.soft_assert(
                    False, "Browser call budget should reject excess calls"
                )
        finally:
            BrowserTool._browse = original_browse
            browser_module.get_browser_max_concurrent_sessions = original_concurrency
            browser_module.get_browser_max_calls_per_turn = original_call_limit
            browser_module.get_browser_min_memory_headroom_bytes = original_headroom
            browser_module.read_cgroup_memory_status = original_memory
            BrowserTool._session_semaphore = None
            BrowserTool._session_semaphore_limit = 0
            BrowserTool._active_sessions = 0
            BrowserTool._call_counts.clear()

        self.teardown_scenario()
        self.assert_no_failures()
