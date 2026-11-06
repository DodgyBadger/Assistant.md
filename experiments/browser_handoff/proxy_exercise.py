"""Real-browser compatibility check behind a controlled Codeman-shaped proxy.

The only external substitute is the local proxy: it strips Authorization,
injects a base path, removes frame-ancestors, and supports opaque-origin CORS.
It does not replace any prototype behavior and does not run Codeman itself.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import secrets
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import httpx
from playwright.async_api import Request, async_playwright, expect

from experiments.browser_handoff.console import Options
from experiments.browser_handoff.exercise import serve
from experiments.browser_handoff.runtime import browser_runtime

PREFIX = "/preview/browser/"


@contextmanager
def proxy_site(upstream: str, sandboxed: bool) -> Iterator[str]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            pass

        def reply(
            self, status: int, content: bytes, content_type: str, csp: str = ""
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
            self.send_header(
                "Access-Control-Allow-Headers",
                self.headers.get(
                    "Access-Control-Request-Headers",
                    "content-type,x-browser-handoff-token,authorization",
                ),
            )
            if csp:
                self.send_header("Content-Security-Policy", csp)
            self.end_headers()
            try:
                self.wfile.write(content)
            except (BrokenPipeError, ConnectionResetError):
                pass  # A closing viewer can disconnect during an in-flight frame.

        def do_OPTIONS(self) -> None:
            self.reply(204, b"", "text/plain")

        def do_GET(self) -> None:
            self.forward()

        def do_POST(self) -> None:
            self.forward()

        def forward(self) -> None:
            if self.path == "/":
                sandbox = 'sandbox="allow-scripts allow-forms"' if sandboxed else ""
                document = f'<iframe title="Prototype" src="{PREFIX}" {sandbox} width="1400" height="1100"></iframe>'
                self.reply(200, document.encode(), "text/html")
                return
            if not self.path.startswith(PREFIX):
                self.reply(404, b"Not found", "text/plain")
                return
            path = self.path[len(PREFIX) :]
            headers = {
                key: value
                for key, value in self.headers.items()
                if key.lower()
                not in {
                    "authorization",
                    "host",
                    "content-length",
                    "connection",
                    "origin",
                    "referer",
                }
            }
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            with httpx.Client(timeout=15) as client:
                response = client.request(
                    self.command, upstream + "/" + path, headers=headers, content=body
                )
            content_type = response.headers.get(
                "Content-Type", "application/octet-stream"
            )
            content = response.content
            if content_type.startswith("text/html"):
                html = (
                    content.decode()
                    .replace('src="/', f'src="{PREFIX}')
                    .replace('href="/', f'href="{PREFIX}')
                )
                html = html.replace("<head>", f'<head><base href="{PREFIX}">', 1)
                content = html.encode()
            csp = "; ".join(
                part.strip()
                for part in response.headers.get("Content-Security-Policy", "").split(
                    ";"
                )
                if part.strip() and not part.strip().startswith("frame-ancestors")
            )
            self.reply(response.status_code, content, content_type, csp)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def escape_recorder(origin: str, escaped: list[str]) -> Callable[[Request], None]:
    def observe(request: Request) -> None:
        if request.url.startswith(origin + "human/"):
            escaped.append("escaped")

    return observe


async def exercise(output: Path) -> int:
    checks: dict[str, bool] = {}
    report: dict[str, object] = {
        "scope": __doc__,
        "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "checks": checks,
    }
    options = Options(
        secrets.token_urlsafe(32), secrets.token_urlsafe(32), mode="headless"
    )
    try:
        async with serve(options) as upstream, async_playwright() as playwright:
            browser = await playwright.chromium.launch(
                headless=True, channel="chromium"
            )
            try:
                for sandboxed in (True, False):
                    label = "sandboxed" if sandboxed else "unsandboxed"
                    with proxy_site(upstream, sandboxed) as origin:
                        context = await browser.new_context(
                            viewport={"width": 1500, "height": 1200}
                        )
                        try:
                            page = await context.new_page()
                            escaped: list[str] = []
                            page.on("request", escape_recorder(origin, escaped))
                            await page.goto(origin)
                            frame = page.frame_locator("iframe")
                            await frame.get_by_label("Access code", exact=True).fill(
                                "invalid-code"
                            )
                            await frame.get_by_role(
                                "button", name="Connect", exact=True
                            ).click()
                            try:
                                await expect(frame.get_by_role("status")).to_have_text(
                                    "unauthorized", timeout=5000
                                )
                            except AssertionError:
                                checks[label + "_invalid_code_rejected"] = False
                                raise AssertionError(
                                    label + "_invalid_code_rejected"
                                ) from None
                            checks[label + "_invalid_code_rejected"] = True
                            await frame.get_by_label("Access code", exact=True).fill(
                                options.human_token
                            )
                            await frame.get_by_role(
                                "button", name="Connect", exact=True
                            ).click()
                            try:
                                await expect(frame.locator("#owner")).to_have_text(
                                    "agent", timeout=5000
                                )
                            except AssertionError:
                                checks[label + "_proxy_connection"] = False
                                raise AssertionError(
                                    label + "_proxy_connection"
                                ) from None
                            checks[label + "_proxy_connection"] = True
                            await expect(
                                frame.get_by_role("img", name="Remote browser")
                            ).to_have_js_property("naturalWidth", 1280)
                            checks[label + "_browser_frame_received"] = True
                            await frame.get_by_role(
                                "button", name="Take control", exact=True
                            ).click()
                            await expect(frame.locator("#owner")).to_have_text("human")
                            await frame.get_by_role(
                                "button", name="Give back", exact=True
                            ).click()
                            await expect(frame.locator("#owner")).to_have_text("agent")
                            checks[label + "_ownership_changes"] = True
                            if escaped:
                                raise AssertionError(
                                    label + "_requests_stay_in_proxy_prefix"
                                )
                            checks[label + "_requests_stay_in_proxy_prefix"] = True
                        finally:
                            await context.close()
            finally:
                await browser.close()
        report["verdict"] = "passed"
    except Exception as exc:  # Safe reporting boundary; do not retain request input.
        report["verdict"] = "failed"
        report["error_type"] = type(exc).__name__
        if isinstance(exc, AssertionError):
            report["failed_check"] = str(exc).split("\n", 1)[0]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0 if report["verdict"] == "passed" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    with browser_runtime("headless"):
        return asyncio.run(exercise(parser.parse_args().output))


if __name__ == "__main__":
    raise SystemExit(main())
