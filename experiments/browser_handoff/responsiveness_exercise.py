"""Real browser regression for a slow preview, repeated takeover and reset."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from time import monotonic
from urllib.parse import urlsplit

import httpx
from playwright.async_api import async_playwright, expect

from experiments.browser_handoff.console import Options
from experiments.browser_handoff.exercise import serve
from experiments.browser_handoff.runtime import browser_runtime


async def exercise(output: Path) -> int:
    checks: dict[str, bool] = {}
    report: dict[str, object] = {"checks": checks}
    try:
        async with (
            serve(Options(mode="headless")) as origin,
            async_playwright() as playwright,
        ):
            browser = await playwright.chromium.launch(
                headless=True, channel="chromium"
            )
            try:
                page = await browser.new_page()
                await page.goto(origin)
                await expect(page.locator("#owner")).to_have_text("agent")
                async with httpx.AsyncClient(base_url=origin, timeout=10) as client:
                    demo = (await client.get("/human/demo")).json()
                    site = demo["url"].removesuffix("/login")
                    state = (await client.get("/human/state")).json()
                    state = (
                        await client.post(
                            "/human/control",
                            json={"owner": "human", "revision": state["revision"]},
                        )
                    ).json()
                    state = (
                        await client.post(
                            "/human/navigate",
                            json={"url": site + "/font", "revision": state["revision"]},
                        )
                    ).json()
                    deadline = monotonic() + 5
                    while (await client.get(site + "/font-state")).text != "requested":
                        if monotonic() > deadline:
                            raise AssertionError("slow_font_fixture_not_requested")
                        await asyncio.sleep(0.05)
                    preview = asyncio.create_task(client.get("/human/frame"))
                    try:
                        rendered = await asyncio.wait_for(asyncio.shield(preview), 0.75)
                        checks["preview_available_while_font_pending"] = (
                            rendered.status_code == 200
                            and rendered.headers.get("content-type") == "image/jpeg"
                        )
                    except TimeoutError:
                        preview.cancel()
                        await asyncio.gather(preview, return_exceptions=True)
                        raise AssertionError(
                            "preview_waits_for_font_instead_of_showing_current_page"
                        ) from None
                    try:
                        response = await asyncio.wait_for(
                            client.get("/human/state"), 0.75
                        )
                        checks["state_responsive_during_preview"] = (
                            response.status_code == 200
                        )
                        response = await asyncio.wait_for(
                            client.post(
                                "/human/control",
                                json={"owner": "agent", "revision": state["revision"]},
                            ),
                            0.75,
                        )
                        checks["handback_responsive_during_preview"] = (
                            response.status_code == 200
                        )
                        state = response.json()
                        response = await asyncio.wait_for(
                            client.post(
                                "/human/control",
                                json={"owner": "human", "revision": state["revision"]},
                            ),
                            0.75,
                        )
                        checks["retake_responsive_during_preview"] = (
                            response.status_code == 200
                        )
                        state = response.json()
                    except TimeoutError:
                        checks["controls_blocked_by_preview"] = True
                        raise AssertionError("controls_blocked_by_preview") from None
                    finally:
                        preview.cancel()
                        await asyncio.gather(preview, return_exceptions=True)
                    response = await client.post(
                        "/human/navigate",
                        json={"url": demo["url"], "revision": state["revision"]},
                    )
                    checks["navigation_after_retake"] = response.status_code == 200
                    await expect(page.locator("#owner")).to_have_text("human")
                    old = (await client.get("/human/state")).json()
                    # Close and recreate through the viewer: the new browser must
                    # replace the higher-revision state still held in this page.
                    await page.get_by_role(
                        "button", name="Close browser", exact=True
                    ).click()
                    await expect(page.locator("#owner")).to_have_text("closed")
                    await page.get_by_role(
                        "button", name="Reset browser", exact=True
                    ).click()
                    await expect(page.locator("#owner")).to_have_text("agent")
                    fresh = (await client.get("/human/state")).json()
                    checks["reset_replaces_browser_identity"] = (
                        fresh["session_id"] != old["session_id"]
                    )
                    checks["reset_accepts_lower_revision"] = (
                        fresh["revision"] < old["revision"]
                    )
                    await page.get_by_role(
                        "button", name="Take control", exact=True
                    ).click()
                    await expect(page.locator("#owner")).to_have_text("human")
                    checks["takeover_after_reset"] = True
                    await page.get_by_label("Website", exact=True).fill(demo["url"])
                    async with page.expect_response(
                        lambda response: response.url.endswith("/human/navigate")
                        and response.request.method == "POST"
                    ) as navigation:
                        await page.get_by_role("button", name="Go", exact=True).click()
                    assert (
                        await navigation.value
                    ).status == 200, "viewer_navigation_response"
                    await expect(
                        page.get_by_label("Website", exact=True)
                    ).to_have_value(demo["url"])
                    checks["viewer_navigation_after_reset"] = (
                        await client.get("/human/state")
                    ).json()["url"] == demo["url"]
            finally:
                await browser.close()
        await restart_check(checks)
        if not all(checks.values()):
            raise AssertionError("responsiveness_checks")
        report["verdict"] = "passed"
    except Exception as exc:
        report["verdict"] = "failed"
        report["error_type"] = type(exc).__name__
        if isinstance(exc, AssertionError):
            report["failed_check"] = str(exc).split("\n", 1)[0]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0 if report["verdict"] == "passed" else 1


async def restart_check(checks: dict[str, bool]) -> None:
    async with async_playwright() as playwright:
        viewer = await playwright.chromium.launch(headless=True, channel="chromium")
        try:
            page = await viewer.new_page()
            async with serve(Options(mode="headless")) as origin:
                port = urlsplit(origin).port
                assert port is not None
                await page.goto(origin)
                await expect(page.locator("#owner")).to_have_text("agent")
                await page.get_by_role(
                    "button", name="Take control", exact=True
                ).click()
                await expect(page.locator("#owner")).to_have_text("human")
                await page.get_by_role("button", name="Give back", exact=True).click()
                await expect(page.locator("#owner")).to_have_text("agent")
                await page.get_by_role(
                    "button", name="Take control", exact=True
                ).click()
                await expect(page.locator("#owner")).to_have_text("human")
            async with serve(Options(mode="headless"), port=port):
                await expect(page.locator("#owner")).to_have_text(
                    "agent", timeout=10000
                )
                checks["viewer_recovers_after_server_restart"] = True
                await page.get_by_role(
                    "button", name="Take control", exact=True
                ).click()
                await expect(page.locator("#owner")).to_have_text("human")
                checks["takeover_after_server_restart"] = True
        finally:
            await viewer.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    with browser_runtime("headless"):
        return asyncio.run(exercise(parser.parse_args().output))


if __name__ == "__main__":
    raise SystemExit(main())
