"""Reproduce the Obscura/Playwright routing incompatibility on a dynamic page."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from validation.core.base_scenario import BaseScenario

_TARGET_URL = "https://quotes.toscrape.com/js/"


class ObscuraPlaywrightInterceptionProbeScenario(BaseScenario):
    """Compare dynamic extraction with and without Playwright request routing."""

    async def test_scenario(self) -> None:
        executable = self._resolve_executable()
        version = await self._read_version(executable)
        baseline = await self._probe(executable, install_route=False)
        routed = await self._probe(executable, install_route=True)
        evidence = {"version": version, "baseline": baseline, "routed": routed}
        (self.artifacts_dir / "obscura-playwright-interception.json").write_text(
            json.dumps(evidence, indent=2, sort_keys=True),
            encoding="utf-8",
        )

        self.soft_assert(
            baseline["quote_count"] >= 10,
            "Obscura should execute the target's JavaScript without routing",
        )
        self.soft_assert_equal(
            routed["quote_count"],
            0,
            "The known routing blocker no longer reproduces; reevaluate the backend plan",
        )
        self.soft_assert_equal(
            len(routed["intercepted_requests"]),
            1,
            "The known blocker should expose only the top-level document to Playwright",
        )

        self.teardown_scenario()
        self.assert_no_failures()

    async def _probe(self, executable: str, *, install_route: bool) -> dict[str, Any]:
        intercepted_requests: list[dict[str, str]] = []
        async with self._obscura_server(executable) as endpoint:
            async with async_playwright() as playwright:
                browser = await playwright.chromium.connect_over_cdp(endpoint)
                context = await browser.new_context(accept_downloads=False)
                page = await context.new_page()
                if install_route:

                    async def continue_request(route: Any) -> None:
                        intercepted_requests.append(
                            {
                                "method": route.request.method,
                                "resource_type": route.request.resource_type,
                                "url": route.request.url,
                            }
                        )
                        await route.continue_()

                    await page.route("**/*", continue_request)
                try:
                    await page.goto(
                        _TARGET_URL,
                        wait_until="domcontentloaded",
                        timeout=15_000,
                    )
                except Exception:
                    # Obscura v0.2.3 does not consistently complete Playwright's
                    # navigation lifecycle even when the document is usable.
                    pass
                await asyncio.sleep(2)
                quote_count = int(
                    await page.evaluate("document.querySelectorAll('.quote').length")
                )
                result = {
                    "final_url": page.url,
                    "quote_count": quote_count,
                    "intercepted_requests": intercepted_requests,
                }
                await context.close()
                await browser.close()
                return result

    @staticmethod
    @asynccontextmanager
    async def _obscura_server(executable: str) -> AsyncIterator[str]:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reserved:
            reserved.bind(("127.0.0.1", 0))
            port = int(reserved.getsockname()[1])
        endpoint = f"http://127.0.0.1:{port}"
        process = await asyncio.create_subprocess_exec(
            executable,
            "serve",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            await ObscuraPlaywrightInterceptionProbeScenario._wait_until_ready(
                process,
                endpoint,
            )
            yield endpoint
        finally:
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=1)
                except TimeoutError:
                    process.kill()
                    await process.wait()

    @staticmethod
    async def _wait_until_ready(
        process: asyncio.subprocess.Process,
        endpoint: str,
    ) -> None:
        async with httpx.AsyncClient(timeout=0.25, trust_env=False) as client:
            for _ in range(40):
                if process.returncode is not None:
                    stderr = await process.stderr.read() if process.stderr else b""
                    raise RuntimeError(
                        "Obscura exited before becoming ready: "
                        + stderr.decode("utf-8", errors="replace")
                    )
                try:
                    response = await client.get(f"{endpoint}/json/version")
                    response.raise_for_status()
                    return
                except httpx.HTTPError:
                    await asyncio.sleep(0.05)
        raise RuntimeError("Obscura did not become ready within two seconds")

    @staticmethod
    def _resolve_executable() -> str:
        configured = os.environ.get("OBSCURA_PROBE_EXECUTABLE")
        candidates = [configured] if configured else []
        candidates.extend([shutil.which("obscura"), str(Path(".venv/bin/obscura"))])
        for candidate in candidates:
            if candidate and Path(candidate).is_file():
                return str(Path(candidate).resolve())
        raise RuntimeError(
            "Obscura is required for this opt-in probe; set OBSCURA_PROBE_EXECUTABLE"
        )

    @staticmethod
    async def _read_version(executable: str) -> str:
        process = await asyncio.create_subprocess_exec(
            executable,
            "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            raise RuntimeError(
                "Obscura version check failed: "
                + stderr.decode("utf-8", errors="replace")
            )
        return stdout.decode("utf-8", errors="replace").strip()
