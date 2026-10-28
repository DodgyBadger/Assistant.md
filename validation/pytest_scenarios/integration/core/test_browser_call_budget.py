"""Browser call budgets reject excess work before any browser is launched."""

import pytest

from validation.pytest_scenarios.application import Application

pytestmark = pytest.mark.scenario


async def test_browser_budget_is_scoped_to_actual_execution_task(
    app: Application,
) -> None:
    await app.start()
    from pydantic_ai.messages import ToolReturn

    from core.identity import LOCAL_USER_AUTHORITY
    from core.runtime.execution_tasks import ExecutionTaskKind, ExecutionTaskSource
    from core.runtime.state import get_runtime_context
    from core.tools.browser import BrowserTool

    for key, value in (
        ("browser_max_calls_per_turn", "2"),
        ("browser_min_memory_headroom_mb", "0"),
    ):
        response = app.request(
            f"/api/system/settings/general/{key}", "PUT", {"value": value}
        )
        assert response.status_code == 200
    tool = BrowserTool.get_tool()
    coordinator = get_runtime_context().task_coordinator
    checkpoint = app.events.checkpoint()
    async with coordinator.track_current_task(
        kind=ExecutionTaskKind.CHAT,
        scope="browser-budget:first",
        source=ExecutionTaskSource.API,
        label="browser budget",
        authority=LOCAL_USER_AUTHORITY,
    ):
        for _ in range(2):
            # URL validation rejects this input before Playwright is imported.
            result = await tool.function(url="ftp://invalid.example/resource")
            assert (
                isinstance(result, ToolReturn) and result.metadata["status"] == "failed"
            )
        result = await tool.function(url="ftp://invalid.example/resource")
        assert isinstance(result, ToolReturn) and result.metadata["status"] == "failed"
    app.events.require(
        "browser_launch_refused",
        after=checkpoint,
        reason="task_call_limit",
        call_count=3,
        call_limit=2,
    )
    checkpoint = app.events.checkpoint()
    async with coordinator.track_current_task(
        kind=ExecutionTaskKind.CHAT,
        scope="browser-budget:second",
        source=ExecutionTaskSource.API,
        label="new browser budget",
        authority=LOCAL_USER_AUTHORITY,
    ):
        result = await tool.function(url="ftp://invalid.example/resource")
        assert isinstance(result, ToolReturn) and result.metadata["status"] == "failed"
    assert not any(
        row["name"] == "browser_launch_refused"
        for row in app.events.select(after=checkpoint)
    )
