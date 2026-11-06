"""Opt-in acceptance exercise for the real standalone prototype, not the main app.

Run with browser runtime dependencies installed:
    uv run python -m experiments.browser_handoff.exercise --output PATH
The local synthetic website is the only external-site substitute. Both browsers,
the rendered console, HTTP control routes, ownership and form submission are real.
Reports contain named checks and source hashes, never credentials, input or frames.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import secrets
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic

import httpx
import uvicorn
from playwright.async_api import async_playwright, expect

from experiments.browser_handoff.console import Options, create_app
from experiments.browser_handoff.runtime import browser_runtime


@asynccontextmanager
async def serve(options: Options) -> AsyncIterator[str]:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        server = uvicorn.Server(
            uvicorn.Config(create_app(options), access_log=False, log_level="error")
        )
        task = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            deadline = monotonic() + 30
            while not server.started:
                if task.done():
                    await task
                    raise RuntimeError("Prototype exited before startup")
                if monotonic() > deadline:
                    raise TimeoutError("Prototype did not start")
                await asyncio.sleep(0.05)
            yield f"http://127.0.0.1:{port}"
        finally:
            server.should_exit = True
            await asyncio.wait_for(task, timeout=20)


async def exercise(checks: dict[str, bool]) -> None:
    def require(condition: bool, name: str) -> None:
        checks[name] = condition
        if not condition:
            raise AssertionError(name)

    options = Options(secrets.token_urlsafe(32), secrets.token_urlsafe(32))
    async with serve(options) as origin, async_playwright() as playwright:
        viewer = await playwright.chromium.launch(headless=True, channel="chromium")
        try:
            page = await viewer.new_page(viewport={"width": 1440, "height": 1100})
            await page.goto(origin + "/#" + options.human_token)
            require(
                await page.get_by_role(
                    "button", name="Take control", exact=True
                ).count()
                == 1,
                "takeover_control_available",
            )
            async with (
                httpx.AsyncClient(base_url=origin, timeout=15) as anonymous,
                httpx.AsyncClient(
                    base_url=origin,
                    timeout=15,
                    headers={"Authorization": "Bearer " + options.agent_token},
                ) as agent,
                httpx.AsyncClient(
                    base_url=origin,
                    timeout=15,
                    headers={"Authorization": "Bearer " + options.human_token},
                ) as human,
            ):
                require(
                    (await anonymous.get("/human/state")).status_code == 401,
                    "human_capability_required",
                )
                require(
                    (await agent.get("/human/state")).status_code == 401,
                    "agent_cannot_use_human_capability",
                )
                require(
                    (await human.get("/agent/state")).status_code == 401,
                    "human_token_not_agent_token",
                )
                initial = (await agent.get("/agent/state")).json()
                view = await agent.post(
                    "/agent",
                    json={"operation": "read", "revision": initial["revision"]},
                )
                require(view.status_code == 200, "initial_agent_read")
                stale_revision = view.json()["revision"]
                await page.get_by_role(
                    "button", name="Open demo sign-in", exact=True
                ).click()
                await expect(page.locator("#owner")).to_have_text("human")
                preview = page.get_by_role("img", name="Remote browser", exact=True)
                await preview.wait_for()
                await expect(preview).to_have_js_property("naturalWidth", 1280)
                denied = await agent.post(
                    "/agent", json={"operation": "read", "revision": stale_revision}
                )
                require(
                    denied.status_code == 409
                    and denied.json()["detail"] == "human_in_control",
                    "agent_read_paused",
                )
                denied = await agent.post(
                    "/agent",
                    json={
                        "operation": "fill",
                        "revision": stale_revision,
                        "element_id": 1,
                        "value": "must not execute",
                    },
                )
                require(
                    denied.status_code == 409
                    and denied.json()["detail"] == "human_in_control",
                    "agent_write_paused",
                )
                denied = await agent.get("/human/frame")
                require(denied.status_code == 401, "agent_cannot_read_human_frames")
                demo = (await human.get("/human/demo")).json()
                password = demo["password"]
                # Click the remote body's margin, then use real viewer keyboard
                # events. Input travels through the human HTTP API, not the agent.
                await preview.click(position={"x": 1, "y": 1})
                await page.keyboard.press("Tab")
                await page.keyboard.type(password)
                async with page.expect_response(
                    lambda response: response.url.endswith("/human/control")
                    and response.request.method == "POST"
                ) as pending:
                    await page.get_by_role(
                        "button", name="Give back", exact=True
                    ).click()
                refused = await pending.value
                require(refused.status == 409, "populated_password_prevents_handback")
                require(
                    (await human.get("/human/state")).json()["owner"] == "human",
                    "refused_handback_keeps_human_control",
                )
                await preview.focus()
                await page.keyboard.press("Enter")
                deadline = monotonic() + 10
                while (
                    not (await human.get("/human/state"))
                    .json()["url"]
                    .endswith("/workspace")
                ):
                    if monotonic() > deadline:
                        raise AssertionError("human_signin_through_viewer")
                    await asyncio.sleep(0.1)
                require(True, "human_signin_through_viewer")
                await page.get_by_role("button", name="Give back", exact=True).click()
                await expect(page.locator("#owner")).to_have_text("agent")
                denied = await agent.post(
                    "/agent",
                    json={
                        "operation": "click",
                        "revision": stale_revision,
                        "element_id": 1,
                    },
                )
                require(
                    denied.status_code == 409
                    and denied.json()["detail"] == "stale_view",
                    "pre_takeover_actions_invalidated",
                )
                state = (await agent.get("/agent/state")).json()
                view = (
                    await agent.post(
                        "/agent",
                        json={"operation": "read", "revision": state["revision"]},
                    )
                ).json()
                require(
                    "Private workspace" in view["text"],
                    "agent_reads_authenticated_page",
                )
                require(
                    password not in json.dumps(view),
                    "credential_absent_from_agent_view",
                )
                for operation, label, value in (
                    ("fill", "Project", "Browser feasibility"),
                    ("select", "Category", "research"),
                    ("click", "Save project", ""),
                ):
                    target = next(
                        element
                        for element in view["elements"]
                        if element["label"] == label
                    )
                    response = await agent.post(
                        "/agent",
                        json={
                            "operation": operation,
                            "revision": view["revision"],
                            "element_id": target["id"],
                            "value": value,
                        },
                    )
                    require(response.status_code == 200, "agent_" + operation)
                    view = response.json()
                require(
                    "Saved Browser feasibility (research)" in view["text"],
                    "authenticated_form_submission",
                )
                require(
                    password not in json.dumps(view),
                    "credential_absent_after_submission",
                )
                other = await anonymous.get(
                    demo["url"].removesuffix("/login") + "/workspace"
                )
                require(
                    other.status_code == 401, "unrelated_client_has_no_browser_session"
                )
                await page.get_by_role(
                    "button", name="Close browser", exact=True
                ).click()
                await expect(page.locator("#owner")).to_have_text("closed")
                state = (await human.get("/human/state")).json()
                require(state["page_closed"], "remote_page_really_closed")
                require(
                    (await human.get("/human/frame")).status_code == 410,
                    "closed_session_frames_denied",
                )
                denied = await agent.post(
                    "/agent", json={"operation": "read", "revision": view["revision"]}
                )
                require(denied.status_code == 410, "closed_session_agent_access_denied")
        finally:
            await viewer.close()


async def run(output: Path) -> int:
    checks: dict[str, bool] = {}
    report: dict[str, object] = {
        "started_at": datetime.now(UTC).isoformat(),
        "scope": "Real standalone prototype and Chromium; synthetic external website; no AssistantMD chat integration",
        "exercise_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "checks": checks,
    }
    try:
        await exercise(checks)
        report["verdict"] = "passed"
    except Exception as exc:  # Experiment boundary: preserve safe failure evidence.
        report["verdict"] = "failed"
        report["error_type"] = type(exc).__name__
        if isinstance(exc, AssertionError):
            report["failed_check"] = str(exc)
    report["finished_at"] = datetime.now(UTC).isoformat()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0 if report["verdict"] == "passed" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    with browser_runtime("headful_cdp"):
        return asyncio.run(run(parser.parse_args().output))


if __name__ == "__main__":
    raise SystemExit(main())
