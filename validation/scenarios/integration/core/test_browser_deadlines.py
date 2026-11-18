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


async def test_committed_page_read_deadline_retains_same_page_for_help(
    app: Application, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = app.vault("BrowserRecoverableDeadlineVault")
    await configure(app, "1")
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


async def test_uncommitted_navigation_does_not_offer_help_for_requested_site(
    app: Application, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = app.vault("BrowserUncommittedDeadlineVault")
    await configure(app)
    with retained_browser_site(app, monkeypatch) as site:
        try:
            before = None
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
            assert deadline.metadata["page_retained"] is True
            assert deadline.metadata["retained_url"] == site.url("/prime")
            current = await browser_turn(app, vault.name, "uncommitted")
            assert (
                isinstance(current, str)
                and json.loads(current)["browser_id"]
                == json.loads(before)["browser_id"]
            )
        finally:
            site.release_slow.set()
            await browser_turn(app, vault.name, "uncommitted", operation="close")


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
