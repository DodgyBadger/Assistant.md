"""Opt-in real-browser characterization, independent of AssistantMD runtime.

Run: uv run python -m experiments.browser_handoff.probe --help
Requires Playwright Chromium; starts Xvfb on Linux if needed. No personal profiles,
real logins, storage exports, screenshots, traces, or request bodies are retained.
"""

from __future__ import annotations

import argparse
import asyncio
import ipaddress
import json
import socket
import subprocess
from collections import Counter
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
from urllib.parse import urlsplit

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    Route,
    async_playwright,
)
from playwright.async_api import Error as PlaywrightError

from experiments.browser_handoff.fixture import login_site
from experiments.browser_handoff.runtime import browser_runtime

MODES = ("headless", "headful", "headful_cdp")
SITES = {
    "control": (
        "https://example.com/",
        "body",
        "This domain is for use in documentation examples",
    ),
    "amazon": ("https://www.amazon.ca/dp/B00NH13K8S", "#productTitle", ""),
    "amazon_search": (
        "https://www.amazon.ca/s?k=usb+c+cable",
        '[data-component-type="s-search-result"]',
        "",
    ),
    "digikey": ("https://www.digikey.ca/en/products", "h1", "Product Index"),
}


class AgentPage:
    """Cooperative experiment boundary, not isolation from unrestricted code."""

    def __init__(self, page: Page) -> None:
        self._page = page
        self.human_held = False

    def require_agent(self) -> None:
        if self.human_held:
            raise PermissionError("Human currently controls the browser")

    async def read(self) -> str:
        self.require_agent()
        return await self._page.locator("main").inner_text()

    async def fill(self, label: str, text: str) -> None:
        self.require_agent()
        await self._page.get_by_label(label, exact=True).fill(text)

    async def select(self, label: str, value: str) -> None:
        self.require_agent()
        await self._page.get_by_label(label, exact=True).select_option(value)

    async def click(self, name: str) -> None:
        self.require_agent()
        await self._page.get_by_role("button", name=name, exact=True).click()


async def handoff_checks(browser: Browser) -> dict[str, bool]:
    """Simulate human input through real Chromium; no human UX is exercised."""
    with login_site() as (origin, password):
        context = await browser.new_context(
            accept_downloads=False, service_workers="block"
        )
        other = await browser.new_context(
            accept_downloads=False, service_workers="block"
        )
        try:

            async def fixture_only(route: Route) -> None:
                if route.request.url.startswith(origin + "/"):
                    await route.continue_()
                else:
                    await route.abort()

            await context.route("**/*", fixture_only)
            await other.route("**/*", fixture_only)
            page = await context.new_page()
            page.set_default_timeout(5000)
            denied = await page.goto(origin + "/workspace")
            assert denied is not None and denied.status == 401
            await page.goto(origin + "/login")
            agent = AgentPage(page)
            agent.human_held = True
            blocked = 0
            for operation in (
                agent.read(),
                agent.fill("Password", "agent must not type"),
                agent.select("Category", "research"),
                agent.click("Sign in"),
            ):
                try:
                    await operation
                except PermissionError:
                    blocked += 1
            assert blocked == 4, "Agent operations must fail while the human owns input"

            # Synthetic human-role actor has the page handle; the agent never gets
            # this password. This is deliberately not a claim of process isolation.
            await page.get_by_label("Password", exact=True).fill(password)
            await page.get_by_role("button", name="Sign in", exact=True).click()
            await page.wait_for_url(origin + "/workspace")
            assert await page.locator('input[type="password"]').count() == 0
            agent.human_held = False
            agent_text = await agent.read()
            assert "Private workspace" in agent_text
            assert password not in agent_text
            await agent.fill("Project", "Browser feasibility")
            await agent.select("Category", "research")
            await agent.click("Save project")
            await page.locator("output").filter(
                has_text="Saved Browser feasibility (research)"
            ).wait_for()
            agent_text += await agent.read()
            assert password not in agent_text

            unrelated = await other.new_page()
            denied = await unrelated.goto(origin + "/workspace")
            assert denied is not None and denied.status == 401
            await agent.click("Sign out")
            await page.get_by_text("Sign in required", exact=True).wait_for()
            denied = await page.reload()
            assert denied is not None and denied.status == 401
        finally:
            await other.close()
            await context.close()
        assert page.is_closed() and unrelated.is_closed()
    return dict.fromkeys(
        (
            "unauthenticated_denied",
            "agent_paused_during_takeover",
            "authenticated_read_after_handback",
            "password_absent_from_agent_result",
            "agent_form_submission",
            "separate_context_denied",
            "logout_revokes_access",
            "contexts_closed",
        ),
        True,
    )


class BrowserInstallError(RuntimeError):
    """The selected Playwright installation has no managed Chromium binary."""


def chromium_executable(playwright: Playwright) -> str:
    executable = Path(playwright.chromium.executable_path)
    if not executable.is_file():
        raise BrowserInstallError(
            f"Playwright Chromium is missing at {executable}.\n"
            "Install it for this checkout and user with:\n"
            "  uv run python -m playwright install chromium\n"
            "Then retry: scripts/dev run --browser-handoff"
        )
    return str(executable)


@asynccontextmanager
async def launch(playwright: Playwright, mode: str) -> AsyncIterator[Browser]:
    executable = chromium_executable(playwright)
    if mode != "headful_cdp":
        browser = await playwright.chromium.launch(
            headless=mode == "headless", channel="chromium"
        )
        try:
            yield browser
        finally:
            await browser.close()
        return
    with TemporaryDirectory(prefix="assistantmd-browser-probe-") as profile:
        process = subprocess.Popen(
            [
                executable,
                "--remote-debugging-port=0",
                "--remote-debugging-address=127.0.0.1",
                f"--user-data-dir={profile}",
                "--no-first-run",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--no-default-browser-check",
                "--disable-blink-features=AutomationControlled",
                "--window-size=1440,1000",
                "about:blank",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            port_file = Path(profile) / "DevToolsActivePort"
            deadline = monotonic() + 20
            while not port_file.exists():
                if process.poll() is not None:
                    raise RuntimeError(
                        "Standalone Chromium exited before publishing CDP"
                    )
                if monotonic() > deadline:
                    raise TimeoutError("Standalone Chromium did not publish CDP")
                await asyncio.sleep(0.1)
            port = int(port_file.read_text().splitlines()[0])
            browser = await playwright.chromium.connect_over_cdp(
                f"http://127.0.0.1:{port}"
            )
            try:
                yield browser
            finally:
                await browser.close()
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    await asyncio.to_thread(process.wait, 5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    await asyncio.to_thread(process.wait)


async def diagnostics(browser: Browser) -> dict[str, object]:
    context = await browser.new_context()
    try:
        page = await context.new_page()
        values = await page.evaluate(
            """() => ({user_agent: navigator.userAgent, webdriver: navigator.webdriver, language: navigator.language, timezone: Intl.DateTimeFormat().resolvedOptions().timeZone})"""
        )
        if not isinstance(values, dict):
            raise TypeError("Browser diagnostics did not return an object")
        return {str(key): value for key, value in values.items()}
    finally:
        await context.close()


def public_host(url: str) -> bool:
    parsed = urlsplit(url)
    if (
        parsed.scheme not in {"https", "http"}
        or parsed.username
        or parsed.password
        or not parsed.hostname
    ):
        return False
    try:
        addresses = socket.getaddrinfo(parsed.hostname, None, type=socket.SOCK_STREAM)
    except OSError:
        return False
    return bool(addresses) and all(
        ipaddress.ip_address(row[4][0]).is_global for row in addresses
    )


async def live_probe(
    browser: Browser, site: str, policy: str, settle: float
) -> dict[str, object]:
    url, selector, expected = SITES[site]
    counts: Counter[str] = Counter()
    statuses: list[int] = []
    context: BrowserContext = await browser.new_context(
        accept_downloads=False, service_workers="block"
    )

    # Match resource/method restrictions only. DNS checks are not socket-pinned
    # and do not claim the full production network boundary. No user URLs allowed.
    async def route_request(route: Route) -> None:
        request = route.request
        counts[f"seen_{request.method}"] += 1
        if not await asyncio.to_thread(public_host, request.url):
            counts["blocked_network"] += 1
            await route.abort()
        elif policy == "restricted" and request.method not in {"GET", "HEAD"}:
            counts["blocked_method"] += 1
            await route.abort()
        elif policy == "restricted" and request.resource_type in {
            "image",
            "media",
            "font",
        }:
            counts["blocked_resource"] += 1
            await route.abort()
        else:
            counts[f"allowed_{request.method}"] += 1
            await route.continue_()

    result: dict[str, object] = {"site": site, "policy": policy}
    try:
        await context.route("**/*", route_request)
        page = await context.new_page()
        page.set_default_timeout(5000)
        page.on(
            "response",
            lambda response: (
                statuses.append(response.status)
                if response.request.is_navigation_request()
                and response.frame == page.main_frame
                else None
            ),
        )
        try:
            response = await page.goto(
                url, wait_until="domcontentloaded", timeout=25000
            )
            result["initial_status"] = response.status if response else None
            await asyncio.sleep(settle)
            text = (await page.locator("body").inner_text(timeout=5000)).lower()
            title = await page.title()
            content = page.locator(selector)
            content_ready = (
                await content.count() > 0 and await content.first.is_visible()
            )
            if content_ready and expected:
                content_ready = (
                    expected.lower() in (await content.first.inner_text()).lower()
                )
            result.update(
                {
                    "page_title": title[:160],
                    "first_heading": (
                        (await page.locator("h1").first.inner_text())[:160]
                        if await page.locator("h1").count()
                        else None
                    ),
                    "catalog_link_count": await page.locator(
                        'a[href*="/en/products/filter/"], a[href*="/en/products/detail/"]'
                    ).count(),
                    "content_ready": content_ready,
                    "challenge_phrase_matches": [
                        phrase
                        for phrase in (
                            "just a moment",
                            "verify you are human",
                            "sorry! something went wrong",
                            "robot check",
                            "access denied",
                            "checking your browser",
                        )
                        if phrase in text or phrase in title.lower()
                    ],
                    "captcha_form_present": await page.locator(
                        'form[action*="validateCaptcha"], input#captchacharacters'
                    ).count()
                    > 0,
                    "body_characters": len(text),
                }
            )
        except PlaywrightError as exc:
            # Exception strings can contain challenge tokens/URLs. Keep type only.
            result["error_type"] = type(exc).__name__
        result["navigation_statuses"] = statuses
    finally:
        await context.close()
    result["request_counts"] = dict(counts)
    return result


async def run(args: argparse.Namespace) -> int:
    report: dict[str, object] = {
        "started_at": datetime.now(UTC).isoformat(),
        "scope": "Standalone feasibility; not AssistantMD integration or human UX acceptance",
        "chromium_sandbox": False,
        "transport_limit": "CDP mode is attached through Playwright, which can itself expose automation; this does not reproduce AgenticOS's minimal CDP adapter",
        "modes": [],
    }
    results: list[dict[str, object]] = []
    report["modes"] = results
    exit_code = 0
    async with async_playwright() as playwright:
        for mode in args.mode:
            async with launch(playwright, mode) as browser:
                row: dict[str, object] = {
                    "mode": mode,
                    "browser_version": browser.version,
                }
                results.append(row)
                row["diagnostics"] = await diagnostics(browser)
                try:
                    row["handoff"] = await handoff_checks(browser)
                except (AssertionError, PlaywrightError) as exc:
                    row["handoff_failure"] = type(exc).__name__
                    exit_code = 1
                live: list[dict[str, object]] = []
                row["live"] = live
                for site in args.site:
                    for policy in args.policy:
                        result = await live_probe(browser, site, policy, args.settle)
                        live.append(result)
                        print(json.dumps({"mode": mode, **result}), flush=True)
                print(
                    json.dumps(
                        {
                            "mode": mode,
                            "handoff": row.get("handoff"),
                            "diagnostics": row["diagnostics"],
                        }
                    ),
                    flush=True,
                )
    report["finished_at"] = datetime.now(UTC).isoformat()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(f"Report: {args.output}", flush=True)
    return exit_code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", nargs="+", choices=MODES, default=list(MODES))
    parser.add_argument("--site", nargs="+", choices=tuple(SITES), default=[])
    parser.add_argument(
        "--policy",
        nargs="+",
        choices=("restricted", "interactive"),
        default=["restricted", "interactive"],
    )
    parser.add_argument("--settle", type=float, default=5)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("validation/runs/browser-handoff/report.json"),
    )
    args = parser.parse_args()
    if not 0 <= args.settle <= 30:
        parser.error("--settle must be between 0 and 30 seconds")
    if len(set(args.mode)) != len(args.mode):
        parser.error("Duplicate modes are not allowed")
    with browser_runtime(
        "headful_cdp" if any(mode != "headless" for mode in args.mode) else "headless"
    ):
        return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
