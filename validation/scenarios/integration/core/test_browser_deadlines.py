"""Only confirmed usable browser state can support help after a read deadline."""

from __future__ import annotations

import json

import pytest
from pydantic_ai.messages import ToolReturn

from validation.support.application import Application
from validation.support.browser.retained_site import browser_turn, retained_browser_site

pytestmark = pytest.mark.scenario


async def configure(app: Application, navigation_timeout: str = "1") -> None:
    await app.start()
    for key, value in (
        ("browser_min_memory_headroom_mb", "0"),
        ("browser_navigation_timeout_seconds", navigation_timeout),
    ):
        saved = app.request(
            f"/api/system/settings/general/{key}", "PUT", {"value": value}
        )
        assert saved.status_code == 200


@pytest.mark.parametrize("navigation_timeout", ["1", "20"])
async def test_committed_page_read_deadline_retains_same_page_for_help(
    app: Application, monkeypatch: pytest.MonkeyPatch, navigation_timeout: str
) -> None:
    vault = app.vault("BrowserRecoverableDeadlineVault")
    await configure(app, navigation_timeout)
    with retained_browser_site(app, monkeypatch) as site:
        site.pages["/blocked-ready"] = (
            b'<main>Human step</main><label>Name<input name="name"></label><script src="/slow-script"></script>'
        )
        site.cookie_paths.add("/blocked-ready")
        try:
            deadline = await browser_turn(
                app, vault.name, "deadline-owner", url=site.url("/blocked-ready")
            )
            assert (
                site.script_requested.is_set()
            ), "Real external script loading must explain the readiness deadline"
            assert isinstance(deadline, ToolReturn)
            assert deadline.metadata.get("reason") == "read_deadline"
            assert (
                deadline.metadata["help_eligible"] is True
                and deadline.metadata["page_retained"] is True
            )
            assert "observation_ref" not in deadline.metadata
            current = await browser_turn(app, vault.name, "deadline-owner")
            assert isinstance(current, str)
            observed = json.loads(current)
            assert observed["browser_id"] == deadline.metadata["browser_id"]
            assert observed["page_id"] == deadline.metadata["page_id"]
            assert observed["url"] == site.url("/blocked-ready")
            assert "Human step" in observed["text"]
            authenticated = await browser_turn(
                app, vault.name, "deadline-owner", url=site.url("/state")
            )
            assert isinstance(authenticated, str)
            assert json.loads(authenticated)["text"] == "Signed in"
            assert json.loads(authenticated)["browser_id"] == observed["browser_id"]
            occupied = await browser_turn(
                app, vault.name, "deadline-other", url=site.url("/state")
            )
            assert (
                isinstance(occupied, ToolReturn)
                and occupied.metadata["reason"] == "capacity_limit"
            )
            records = [
                json.loads(line)
                for line in (app.root / "system" / "activity.log")
                .read_text()
                .splitlines()
            ]
            events = [
                record["data"]
                for record in records
                if record.get("data", {}).get("event") == "browser_read_deadline"
            ]
            assert any(
                event["session_id"] == "deadline-owner"
                and event["browser_id"] == observed["browser_id"]
                and event["task_id"]
                and event["help_eligible"]
                for event in events
            )
        finally:
            site.release_script.set()
            await browser_turn(app, vault.name, "deadline-owner", operation="close")


@pytest.mark.parametrize("existing", [False, True])
async def test_uncommitted_navigation_does_not_offer_help_for_requested_site(
    app: Application, monkeypatch: pytest.MonkeyPatch, existing: bool
) -> None:
    vault = app.vault("BrowserUncommittedDeadlineVault")
    await configure(app)
    with retained_browser_site(app, monkeypatch) as site:
        try:
            before = None
            if existing:
                before = await browser_turn(
                    app, vault.name, "uncommitted", url=site.url("/prime")
                )
                assert isinstance(before, str)
            deadline = await browser_turn(
                app, vault.name, "uncommitted", url=site.url("/slow")
            )
            assert (
                isinstance(deadline, ToolReturn)
                and deadline.metadata.get("reason") == "read_deadline"
            )
            assert deadline.metadata["help_eligible"] is False
            assert deadline.metadata["page_retained"] is existing
            if existing:
                assert deadline.metadata["retained_url"] == site.url("/prime")
                current = await browser_turn(app, vault.name, "uncommitted")
                assert (
                    isinstance(current, str)
                    and json.loads(current)["browser_id"]
                    == json.loads(before)["browser_id"]
                )
            else:
                missing = await browser_turn(app, vault.name, "uncommitted")
                assert (
                    isinstance(missing, ToolReturn)
                    and missing.metadata["reason"] == "no_current_page"
                )
                fresh = await browser_turn(
                    app, vault.name, "after-blank", url=site.url("/state")
                )
                assert isinstance(fresh, str)
                await browser_turn(app, vault.name, "after-blank", operation="close")
        finally:
            site.release_slow.set()
            await browser_turn(app, vault.name, "uncommitted", operation="close")


async def test_private_target_is_not_a_human_help_deadline(
    app: Application, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = app.vault("BrowserPolicyDeadlineVault")
    await configure(app)
    with retained_browser_site(app, monkeypatch) as site:
        denied = await browser_turn(
            app, vault.name, "denied", url=f"http://127.0.0.1:{site.port}/denied-target"
        )
        assert (
            isinstance(denied, ToolReturn)
            and denied.metadata["reason"] == "destination_denied"
        )
        assert denied.metadata["help_eligible"] is False
        assert site.private_requests == []


@pytest.mark.parametrize("failed_inspection", [False, True])
async def test_delayed_post_requires_fresh_inspection_without_duplicate_submission(
    app: Application, monkeypatch: pytest.MonkeyPatch, failed_inspection: bool
) -> None:
    vault = app.vault("BrowserUncertainInspectionVault")
    await configure(app)
    with retained_browser_site(app, monkeypatch) as site:
        site.pages["/form"] = (
            b'<form method="post" action="/submit"><input name="name" value="initial"><button>Commit</button></form>'
        )
        site.hold_submission = True
        try:
            first = await browser_turn(
                app, vault.name, "uncertain-inspection", url=site.url("/form")
            )
            assert isinstance(first, str)
            old = next(
                control["ref"]
                for control in json.loads(first)["elements"]
                if control["label"] == "Commit"
            )
            uncertain = await browser_turn(
                app,
                vault.name,
                "uncertain-inspection",
                operation="click",
                reference=old,
            )
            assert (
                isinstance(uncertain, ToolReturn)
                and uncertain.metadata["reason"] == "action_uncertain"
            )
            assert uncertain.metadata["completion_uncertain"] is True
            assert uncertain.metadata["requires_inspection"] is True
            assert uncertain.metadata["phase"] == "browser_action"
            assert "observation_ref" not in uncertain.metadata
            assert site.submissions == [{"name": "initial"}]
            rejected = await browser_turn(
                app,
                vault.name,
                "uncertain-inspection",
                operation="click",
                reference=old,
            )
            assert (
                isinstance(rejected, ToolReturn)
                and rejected.metadata["reason"] == "stale_reference"
            )
            if failed_inspection:
                failed = await browser_turn(
                    app, vault.name, "uncertain-inspection", url=site.url("/slow")
                )
                assert (
                    isinstance(failed, ToolReturn)
                    and failed.metadata["help_eligible"] is False
                )
                assert "observation_ref" not in failed.metadata
                again = await browser_turn(
                    app,
                    vault.name,
                    "uncertain-inspection",
                    operation="click",
                    reference=old,
                )
                assert (
                    isinstance(again, ToolReturn)
                    and again.metadata["reason"] == "stale_reference"
                )
                assert site.receipt_reads == 0 and len(site.submissions) == 1
                site.release_slow.set()
            site.release_submission.set()
            inspected = await browser_turn(
                app, vault.name, "uncertain-inspection", url=site.url("/receipt")
            )
            assert isinstance(inspected, str)
            assert "Recorded submissions: 1" in json.loads(inspected)["text"]
            assert site.receipt_reads == 1 and site.submissions == [{"name": "initial"}]
        finally:
            site.release_submission.set()
            site.release_slow.set()
            await browser_turn(
                app, vault.name, "uncertain-inspection", operation="close"
            )


@pytest.mark.parametrize("stop", [False, True])
async def test_unconfirmed_deadline_cleanup_never_advertises_human_help(
    app: Application, monkeypatch: pytest.MonkeyPatch, stop: bool
) -> None:
    import asyncio
    import os

    from core.runtime.execution_tasks import chat_session_scope
    from core.runtime.state import get_runtime_context
    from validation.support.browser.browser_runtime import descendant_identities
    from validation.support.browser.retained_site import (
        pause_driver_on_browser_event,
        wait_processes_gone,
    )

    vault = app.vault("BrowserUnconfirmedDeadlineVault")
    await configure(app)
    with retained_browser_site(app, monkeypatch) as site:
        baseline = {record["pid"] for record in descendant_identities()}
        first = await browser_turn(
            app, vault.name, "cleanup-deadline", url=site.url("/prime")
        )
        assert isinstance(first, str)
        owned = [
            record
            for record in descendant_identities()
            if record["pid"] not in baseline
        ]
        drivers = [record for record in owned if record["parent"] == os.getpid()]
        assert len(drivers) == 1
        with pause_driver_on_browser_event(
            app, drivers[0]["pid"], "browser_navigation_deadline"
        ) as paused:
            operation = asyncio.create_task(
                browser_turn(app, vault.name, "cleanup-deadline", url=site.url("/slow"))
            )
            try:
                async with asyncio.timeout(5):
                    await paused.ready.wait()
                runtime = get_runtime_context()
                tasks = await runtime.task_coordinator.list_tasks(
                    scope=chat_session_scope("cleanup-deadline"), include_terminal=False
                )
                assert len(tasks) == 1
                if stop:
                    await runtime.task_coordinator.cancel_task(tasks[0].task_id)
                done, _ = await asyncio.wait({operation}, timeout=18)
                assert (
                    operation in done
                ), "Recovery cleanup must release ownership without an IPC reply"
                if stop:
                    with pytest.raises(asyncio.CancelledError):
                        await operation
                    terminal = await runtime.task_coordinator.get_task(tasks[0].task_id)
                    assert terminal is not None and terminal.status == "cancelled"
                else:
                    failed = await operation
                    assert (
                        isinstance(failed, ToolReturn)
                        and failed.metadata["help_eligible"] is False
                    )
                    assert failed.metadata["reason"] == "browser_unresponsive"
                    assert (
                        "browser_id" not in failed.metadata
                        and "page_id" not in failed.metadata
                    )
                assert not app.events.select("browser_read_deadline")
            finally:
                paused.resume()
                site.release_slow.set()
                await asyncio.gather(operation, return_exceptions=True)
        await wait_processes_gone(owned)


async def test_stop_before_navigation_deadline_is_cancellation_without_help(
    app: Application, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    from core.runtime.execution_tasks import chat_session_scope
    from core.runtime.state import get_runtime_context
    from validation.support.browser.browser_runtime import descendant_identities
    from validation.support.browser.retained_site import wait_processes_gone

    vault = app.vault("BrowserNavigationStopVault")
    await configure(app, "20")
    with retained_browser_site(app, monkeypatch) as site:
        site.pages["/ready-stop"] = (
            b'<main>Partial ready page</main><script src="/slow-script"></script>'
        )
        baseline = {record["pid"] for record in descendant_identities()}
        operation = asyncio.create_task(
            browser_turn(
                app, vault.name, "navigation-stop", url=site.url("/ready-stop")
            )
        )
        try:
            async with asyncio.timeout(6):
                while not site.script_requested.is_set():
                    await asyncio.sleep(0.02)
            owned = [
                record
                for record in descendant_identities()
                if record["pid"] not in baseline
            ]
            assert owned
            runtime = get_runtime_context()
            tasks = await runtime.task_coordinator.list_tasks(
                scope=chat_session_scope("navigation-stop"), include_terminal=False
            )
            assert len(tasks) == 1
            await runtime.task_coordinator.cancel_task(tasks[0].task_id)
            async with asyncio.timeout(10):
                with pytest.raises(asyncio.CancelledError):
                    await operation
            terminal = await runtime.task_coordinator.get_task(tasks[0].task_id)
            assert terminal is not None and terminal.status == "cancelled"
            assert not app.events.select("browser_read_deadline")
            await wait_processes_gone(owned)
        finally:
            site.release_script.set()
            await asyncio.gather(operation, return_exceptions=True)


async def test_history_change_on_old_document_cannot_attest_new_navigation_commit(
    app: Application, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = app.vault("BrowserHistoryDeadlineVault")
    await configure(app)
    with retained_browser_site(app, monkeypatch) as site:
        site.pages["/history-owner"] = (
            b'<main>Previous document</main><script>setInterval(() => history.replaceState({}, "", "/changed-old"), 50);</script>'
        )
        try:
            first = await browser_turn(
                app, vault.name, "history-deadline", url=site.url("/history-owner")
            )
            assert isinstance(first, str)
            deadline = await browser_turn(
                app, vault.name, "history-deadline", url=site.url("/slow")
            )
            assert site.slow_started.is_set()
            assert (
                isinstance(deadline, ToolReturn)
                and deadline.metadata["reason"] == "read_deadline"
            )
            assert deadline.metadata["retained_url"] == site.url("/changed-old")
            assert deadline.metadata["help_eligible"] is False
            current = await browser_turn(app, vault.name, "history-deadline")
            assert (
                isinstance(current, str)
                and "Previous document" in json.loads(current)["text"]
            )
            assert json.loads(current)["browser_id"] == json.loads(first)["browser_id"]
        finally:
            site.release_slow.set()
            await browser_turn(app, vault.name, "history-deadline", operation="close")
