"""Opt-in wrong-expectation control; its first case must fail, never a merge gate."""

import pytest
from playwright.async_api import Error, expect

from validation.scenarios.support.browser_journeys import open_settings
from validation.scenarios.support.web_strategy_loopback import endpoint

pytestmark = pytest.mark.scenario


async def test_intentional_failure_retains_real_browser_and_app_evidence(
    browser_app, ui
):
    await open_settings(ui, browser_app.url, "browser_max_calls_per_turn")
    await ui.context.add_cookies(
        [
            {
                "name": "browser_isolation_probe",
                "value": "prior-context",
                "url": browser_app.url,
            }
        ]
    )
    assert any(
        c["name"] == "browser_isolation_probe" for c in await ui.context.cookies()
    )
    browser_app.app.evidence.step("intentional_browser_assertion_failure")
    # This is explicitly an incorrect oracle used to inspect diagnostic output,
    # not missing product behavior and not a substituted application result.
    raise AssertionError(
        "Intentional browser evidence probe; the following case must still pass"
    )


async def test_next_context_is_clean_and_undeclared_origin_receives_no_requests(
    browser_app, ui
):
    assert not any(
        c["name"] == "browser_isolation_probe" for c in await ui.context.cookies()
    )
    card = await open_settings(ui, browser_app.url, "browser_max_calls_per_turn")
    await expect(card.get_by_text("4", exact=True)).to_be_visible()
    with endpoint() as undeclared:
        with pytest.raises(Error):
            await ui.goto(f"http://127.0.0.1:{undeclared.server_port}/blocked")
        assert undeclared.paths == []
    await open_settings(ui, browser_app.url, "browser_max_calls_per_turn")
    assert not (browser_app.vault / "source.md").exists()
