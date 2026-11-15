"""Private browser pixels and native credential input through real handoff."""

from __future__ import annotations

import asyncio
import json

import pytest

from validation.support.application import Application
from validation.support.browser.handoff import handoff_path, waiting_handoff
from validation.support.browser.human_transport import (
    frame_metadata,
    fresh_frame,
    human_transport,
)
from validation.support.browser.retained_site import browser_turn, retained_browser_site

pytestmark = pytest.mark.scenario


async def test_human_enters_and_submits_credentials_without_exporting_them_to_the_agent(
    app: Application, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = app.vault("BrowserPrivateInputVault")
    await app.start()
    assert (
        app.request(
            "/api/system/settings/general/browser_min_memory_headroom_mb",
            "PUT",
            {"value": "0"},
        ).status_code
        == 200
    )
    sentinel = "synthetic-human-password-only-in-browser-61842"
    app.evidence.register_secret(sentinel)
    with retained_browser_site(app, monkeypatch) as site:
        site.sign_in_on_submit = True
        site.pages["/login"] = (
            b"""<style>body{margin:0}input{position:absolute;left:20px;top:20px;width:200px;height:40px}button{position:absolute;left:20px;top:80px;width:140px;height:40px}</style><form method="post" action="/submit"><input type="password" name="password"><button>Sign in</button></form>"""
        )
        initial = await browser_turn(
            app, vault.name, "private-input", url=site.url("/login")
        )
        assert isinstance(initial, str)
        operation = asyncio.create_task(
            browser_turn(app, vault.name, "private-input", operation="handoff")
        )
        try:
            state = await waiting_handoff(app, operation, "private-input")
            path = handoff_path("private-input") + "/" + state["handoff_id"]
            assert (await app.request_async(path + "/claim", "POST")).status_code == 200
            async with human_transport(app) as client:
                frame = await client.get(path + "/frame")
                metadata = frame_metadata(frame)
                assert metadata["page_id"] == state["page_id"]
                clicked = await client.post(
                    path + "/input",
                    json={**metadata, "kind": "click", "x": 50, "y": 40},
                )
                assert clicked.status_code == 200
                assert clicked.json()["requires_frame"]
                metadata = frame_metadata(await client.get(path + "/frame"))
                typed = await client.post(
                    path + "/input", json={**metadata, "kind": "text", "text": sentinel}
                )
                assert typed.status_code == 200 and sentinel not in typed.text
                frame_metadata(await client.get(path + "/frame"))
                refused = await client.post(path + "/return")
                assert (
                    refused.status_code == 409
                    and refused.json()["detail"] == "credentials_pending"
                )
                metadata = frame_metadata(await client.get(path + "/frame"))
                submitted = await client.post(
                    path + "/input",
                    json={**metadata, "kind": "click", "x": 70, "y": 100},
                )
                assert submitted.status_code == 200
                async with asyncio.timeout(5):
                    while not site.submissions:
                        await asyncio.sleep(0.05)
                assert site.submissions == [{"password": sentinel}]
                # Inspect the resulting page before handing back; no image is
                # retained and no input is repeated through a tool call.
                async with asyncio.timeout(5):
                    while True:
                        response = await client.get(path + "/frame")
                        if response.status_code == 200:
                            frame_metadata(response)
                            break
                        await asyncio.sleep(0.05)
                assert (await client.post(path + "/return")).status_code == 200
            result = await operation
            assert isinstance(result, str) and sentinel not in result
            observed = await browser_turn(app, vault.name, "private-input")
            assert isinstance(observed, str) and json.loads(observed)["text"] == "Saved"
            assert sentinel not in observed
            signed_in = await browser_turn(
                app, vault.name, "private-input", url=site.url("/state")
            )
            assert (
                isinstance(signed_in, str)
                and json.loads(signed_in)["text"] == "Signed in"
            )
        finally:
            if not operation.done():
                operation.cancel()
            await asyncio.gather(operation, return_exceptions=True)


async def test_private_input_and_parser_failures_do_not_enter_real_telemetry_or_logs(
    app: Application,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
    caplog: pytest.LogCaptureFixture,
) -> None:
    import base64
    import logging

    import logfire
    from pydantic import BaseModel

    from validation.support.browser.privacy import private_telemetry

    vault = app.vault("BrowserPrivateTelemetryVault")
    await app.start()
    assert (
        app.request(
            "/api/system/settings/general/browser_min_memory_headroom_mb",
            "PUT",
            {"value": "0"},
        ).status_code
        == 200
    )
    sentinel = "synthetic-private-input-telemetry-sentinel-85364"
    app.evidence.register_secret(sentinel)
    monkeypatch.setenv("DEBUGP", "1")
    monkeypatch.setenv("DEBUG", "pw:protocol")
    with retained_browser_site(app, monkeypatch) as site:
        site.pages["/private"] = (
            b'<style>body{margin:0}input{position:absolute;left:20px;top:20px;width:200px;height:40px}</style><input type="password">'
        )
        assert isinstance(
            await browser_turn(
                app, vault.name, "private-telemetry", url=site.url("/private")
            ),
            str,
        )
        operation = asyncio.create_task(
            browser_turn(app, vault.name, "private-telemetry", operation="handoff")
        )
        try:
            state = await waiting_handoff(app, operation, "private-telemetry")
            path = handoff_path("private-telemetry") + "/" + state["handoff_id"]
            with private_telemetry(app) as (application, exporter):

                class PublicValidationProbe(BaseModel):
                    value: str

                PublicValidationProbe(value="public-validation-capture-control")
                logging.getLogger("browser-private-probe").debug(
                    "public-logging-capture-control"
                )
                async with human_transport(app, application=application) as client:
                    assert (
                        await client.get("/api/system/settings/general")
                    ).status_code == 200
                    assert (await client.post(path + "/claim")).status_code == 200

                    private_frame = await client.get(path + "/frame")
                    frame_metadata(private_frame)
                    pixel_marker = base64.b64encode(private_frame.content)[:96].decode()

                    async def apply(kind, **values):
                        metadata = frame_metadata(await client.get(path + "/frame"))
                        response = await client.post(
                            path + "/input", json={**metadata, "kind": kind, **values}
                        )
                        assert response.status_code == 200
                        return response

                    await apply("click", x=50, y=40)
                    await apply("text", text=sentinel)
                    frame_metadata(await client.get(path + "/frame"))
                    refused = await client.post(path + "/return")
                    assert refused.status_code == 409 and sentinel not in refused.text
                    for prefix in (
                        path,
                        path.replace("browser-handoff", "b%72owser-handoff"),
                    ):
                        malformed = await client.post(
                            prefix + "/input?probe=1",
                            content=(
                                b'{"kind":"text","text":"' + sentinel.encode() + b'"'
                            ),
                            headers={"Content-Type": "application/json"},
                        )
                        assert (
                            malformed.status_code == 400
                            and sentinel not in malformed.text
                        )
                        duplicate = await client.post(
                            prefix + "/input",
                            content=json.dumps({"text": sentinel})[:-1]
                            + ',"text":'
                            + json.dumps(sentinel)
                            + "}",
                            headers={"Content-Type": "application/json"},
                        )
                        assert (
                            duplicate.status_code == 400
                            and sentinel not in duplicate.text
                        )
                        oversized = await client.post(
                            prefix + "/input",
                            content=sentinel + "x" * 8192,
                            headers={"Content-Type": "application/json"},
                        )
                        assert (
                            oversized.status_code == 413
                            and sentinel not in oversized.text
                        )
                    assert (
                        await client.get(handoff_path("private-telemetry") + "?probe=1")
                    ).status_code == 200
                    await apply("key", key="SelectAll")
                    await apply("key", key="Backspace")
                    frame_metadata(await client.get(path + "/frame"))
                    assert (await client.post(path + "/return")).status_code == 200
                assert logfire.force_flush()
                spans = exporter.exported_spans
                rendered = repr(
                    [
                        (
                            span.name,
                            dict(span.attributes or {}),
                            [
                                (event.name, dict(event.attributes or {}))
                                for event in span.events
                            ],
                        )
                        for span in spans
                    ]
                )
                validation_captured = "public-validation-capture-control" in rendered
                logging_captured = "public-logging-capture-control" in rendered
                assert validation_captured
                assert logging_captured
                assert any(
                    (span.attributes or {}).get("http.route")
                    == "/api/system/settings/general"
                    for span in spans
                )
                assert not any(
                    "browser-handoff"
                    in str((span.attributes or {}).get("http.route", ""))
                    for span in spans
                )
                telemetry_leaked = sentinel in rendered or pixel_marker in rendered
                assert (
                    not telemetry_leaked
                ), "Private bytes reached unredacted telemetry"
            assert isinstance(await operation, str)
            captured = capsys.readouterr()
            raw_logs = caplog.text
            caplog.clear()
            output_leaked = any(
                value in captured.out + captured.err + raw_logs
                for value in (sentinel, pixel_marker)
            )
            assert not output_leaked, "Private bytes reached raw output/logging"
            assert "SEND>" not in captured.out and "RECV>" not in captured.out
            for log in (app.root / "system").rglob("*.log"):
                private_log = log.read_text(errors="replace")
                log_leaked = sentinel in private_log or pixel_marker in private_log
                assert not log_leaked, "Private bytes reached application logs"
        finally:
            capsys.readouterr()
            caplog.clear()
            if not operation.done():
                operation.cancel()
            await asyncio.gather(operation, return_exceptions=True)


async def test_input_tokens_are_single_use_and_dead_handoffs_cannot_receive_pixels_or_input(
    app: Application, monkeypatch: pytest.MonkeyPatch
) -> None:
    from validation.support.browser.human_transport import claimed_browser

    async with claimed_browser(app, monkeypatch, session_id="input-replay") as (
        site,
        client,
        operation,
        path,
        state,
    ):
        first_frame = await client.get(path + "/frame")
        first_metadata = frame_metadata(first_frame)
        second_frame = await client.get(path + "/frame")
        metadata = frame_metadata(second_frame)
        assert int(second_frame.headers["x-browser-frame"]) > int(
            first_frame.headers["x-browser-frame"]
        )
        assert (
            metadata == first_metadata
        ), "Passive frames must preserve input admission"
        assert (
            await client.post(
                path + "/input", json={**metadata, "kind": "click", "x": 40, "y": 40}
            )
        ).status_code == 200
        metadata = frame_metadata(await client.get(path + "/frame"))
        payload = {**metadata, "kind": "text", "text": "once"}
        assert (await client.post(path + "/input", json=payload)).status_code == 200
        replay = await client.post(path + "/input", json=payload)
        assert replay.status_code == 409 and replay.json()["detail"] == "stale_input"
        assert (await client.post(path + "/return")).status_code == 409
        metadata = frame_metadata(await client.get(path + "/frame"))
        skipped = await client.post(
            path + "/input",
            json={
                **metadata,
                "sequence": metadata["sequence"] + 1,
                "kind": "key",
                "key": "Enter",
            },
        )
        assert skipped.status_code == 409
        assert (
            await client.post(
                path + "/input", json={**metadata, "kind": "key", "key": "Enter"}
            )
        ).status_code == 200
        async with asyncio.timeout(5):
            while not site.submissions:
                await asyncio.sleep(0.05)
        assert site.submissions == [{"password": "once"}]
        await fresh_frame(client, path)
        assert (await client.post(path + "/return")).status_code == 200
        assert isinstance(await operation, str)
        assert (await client.get(path + "/frame")).status_code == 409
        assert (await client.post(path + "/input", json=payload)).status_code == 409
        assert site.submissions == [{"password": "once"}]


async def test_changed_scroll_geometry_rejects_previously_captured_coordinates(
    app: Application, monkeypatch: pytest.MonkeyPatch
) -> None:
    from validation.support.browser.human_transport import claimed_browser

    html = b'<style>body{height:3000px}</style><input type="password"><script>fetch("/wait-mutation").then(async()=>{scrollTo(0,400);await fetch("/mutation-applied");await fetch("/slow");scrollTo(0,0);fetch("/receipt")})</script>'
    async with claimed_browser(
        app, monkeypatch, session_id="input-geometry", html=html
    ) as (site, client, _, path, _state):
        metadata = frame_metadata(await client.get(path + "/frame"))
        site.mutate.set()
        assert await asyncio.to_thread(site.mutation_applied.wait, 3)
        response = await client.post(
            path + "/input", json={**metadata, "kind": "click", "x": 40, "y": 40}
        )
        assert (
            response.status_code == 409 and response.json()["detail"] == "stale_input"
        )
        site.release_slow.set()
        async with asyncio.timeout(3):
            while not site.receipt_reads:
                await asyncio.sleep(0.02)
        restored = await client.post(
            path + "/input", json={**metadata, "kind": "click", "x": 40, "y": 40}
        )
        assert (
            restored.status_code == 409
        ), "An observed stale coordinate token must not revive when scrolling back"
        fresh = await fresh_frame(client, path)
        assert fresh["view_ref"] != metadata["view_ref"]
        assert fresh["sequence"] == metadata["sequence"]


async def test_frame_polling_does_not_keep_a_handoff_alive(
    app: Application, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pydantic_ai.messages import ToolReturn

    from validation.support.browser.human_transport import claimed_browser

    async with claimed_browser(app, monkeypatch, session_id="frame-idle") as (
        _site,
        client,
        operation,
        path,
        _state,
    ):
        frame_metadata(await client.get(path + "/frame"))
        assert (
            app.request(
                "/api/system/settings/general/browser_idle_timeout_seconds",
                "PUT",
                {"value": "1"},
            ).status_code
            == 200
        )
        async with asyncio.timeout(6):
            while not operation.done():
                await client.get(path + "/frame")
                await asyncio.sleep(0.05)
            failed = await operation
        assert (
            isinstance(failed, ToolReturn)
            and failed.metadata["reason"] == "browser_expired"
        )


@pytest.mark.parametrize("finish", ["cancel", "deadline"])
async def test_partial_private_body_has_bounded_admission_and_never_dispatches(
    app: Application, monkeypatch: pytest.MonkeyPatch, finish: str
) -> None:
    from validation.support.browser.human_transport import claimed_browser

    async with claimed_browser(app, monkeypatch, session_id="input-body") as (
        site,
        client,
        _operation,
        path,
        _state,
    ):
        metadata = frame_metadata(await client.get(path + "/frame"))
        assert (
            await client.post(
                path + "/input", json={**metadata, "kind": "click", "x": 40, "y": 40}
            )
        ).status_code == 200
        metadata = frame_metadata(await client.get(path + "/frame"))
        sentinel = "synthetic-incomplete-input-must-not-be-inserted"
        app.evidence.register_secret(sentinel)
        raw = json.dumps({**metadata, "kind": "text", "text": sentinel}).encode()
        entered, release = asyncio.Event(), asyncio.Event()

        async def body():
            yield raw[:-1]
            entered.set()
            await release.wait()
            yield raw[-1:]

        request = asyncio.create_task(
            client.post(
                path + "/input",
                content=body(),
                headers={"Content-Type": "application/json"},
            )
        )
        try:
            async with asyncio.timeout(3):
                await entered.wait()
            async with asyncio.timeout(0.5):
                busy = await client.get(path + "/frame")
            assert busy.status_code == 409 and busy.json()["detail"] == "browser_busy"
            if finish == "cancel":
                request.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await request
            else:
                async with asyncio.timeout(3):
                    invalid = await request
                assert invalid.status_code == 400
            completed = await client.post(
                path + "/input", json={**metadata, "kind": "text", "text": "complete"}
            )
            assert completed.status_code == 200
            fresh = frame_metadata(await client.get(path + "/frame"))
            assert (
                await client.post(
                    path + "/input", json={**fresh, "kind": "key", "key": "Enter"}
                )
            ).status_code == 200
            async with asyncio.timeout(5):
                while not site.submissions:
                    await asyncio.sleep(0.05)
            assert site.submissions == [{"password": "complete"}]
        finally:
            release.set()
            if not request.done():
                request.cancel()
            await asyncio.gather(request, return_exceptions=True)


@pytest.mark.parametrize("finish", ["cancel", "stop"])
async def test_cancelled_or_stopped_capture_keeps_ipc_owned_and_discards_late_pixels(
    app: Application, monkeypatch: pytest.MonkeyPatch, finish: str
) -> None:
    import os
    import signal

    from core.runtime.state import get_runtime_context
    from validation.support.browser.human_transport import (
        assert_viewer_tasks_settled,
        claimed_browser,
        driver_processes,
    )

    async with claimed_browser(app, monkeypatch, session_id="private-capture") as (
        _site,
        client,
        operation,
        path,
        state,
    ):
        old = frame_metadata(await client.get(path + "/frame"))
        driver, _owned = driver_processes()
        os.kill(driver["pid"], signal.SIGSTOP)
        capturing = asyncio.create_task(client.get(path + "/frame"))
        try:
            await asyncio.sleep(0.1)
            async with asyncio.timeout(0.5):
                busy = await client.get(path + "/frame")
            assert busy.status_code == 409 and busy.json()["detail"] == "browser_busy"
            if finish == "cancel":
                capturing.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await capturing
                # Cancelling the caller must not release the actual SDK slot.
                async with asyncio.timeout(0.5):
                    unresolved = await client.get(path + "/frame")
                assert (
                    unresolved.status_code == 409
                    and unresolved.json()["detail"] == "browser_busy"
                )
            else:
                await get_runtime_context().task_coordinator.cancel_task(
                    state["task_id"]
                )
                with pytest.raises(asyncio.CancelledError):
                    await operation
            os.kill(driver["pid"], signal.SIGCONT)
            if finish == "stop":
                response = await capturing
                assert (
                    response.status_code == 409
                    and response.headers["content-type"] != "image/jpeg"
                )
                assert (await client.get(path + "/frame")).status_code == 409
            else:
                fresh = await fresh_frame(client, path)
                assert fresh["input_ref"] != old["input_ref"]
                replay = await client.post(
                    path + "/input", json={**old, "kind": "click", "x": 40, "y": 40}
                )
                assert replay.status_code == 409
                assert (await client.post(path + "/return")).status_code == 200
                assert isinstance(await operation, str)
            await assert_viewer_tasks_settled()
        finally:
            try:
                os.kill(driver["pid"], signal.SIGCONT)
            except ProcessLookupError:
                pass
            if not capturing.done():
                capturing.cancel()
            await asyncio.gather(capturing, return_exceptions=True)


async def test_uncertain_pressed_gesture_retires_its_page_without_replay(
    app: Application, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pydantic_ai.messages import ToolReturn

    from validation.support.browser.human_transport import (
        assert_viewer_tasks_settled,
        claimed_browser,
        driver_processes,
    )
    from validation.support.browser.retained_site import wait_processes_gone

    html = b"<style>body{margin:0}button{position:absolute;left:20px;top:20px;width:140px;height:40px}</style><button onmousedown=\"let request=new XMLHttpRequest();request.open('GET','/slow',false);request.send()\">Hold</button>"
    async with claimed_browser(
        app, monkeypatch, session_id="private-held-gesture", html=html
    ) as (site, client, operation, path, _state):
        metadata = frame_metadata(await client.get(path + "/frame"))
        _driver, owned = driver_processes()
        dispatch = asyncio.create_task(
            client.post(
                path + "/input", json={**metadata, "kind": "click", "x": 50, "y": 40}
            )
        )
        try:
            assert await asyncio.to_thread(
                site.slow_started.wait, 3
            ), "Real mouse press did not reach the website"
            async with asyncio.timeout(15):
                response = await dispatch
            assert (
                response.status_code == 409
                and response.json()["requires_fresh_session"]
            )
            assert response.json()["detail"] == "input_uncertain"
            async with asyncio.timeout(5):
                failed = await operation
            assert (
                isinstance(failed, ToolReturn) and failed.metadata["status"] == "failed"
            )
            assert (
                await client.post(
                    path + "/input",
                    json={**metadata, "kind": "click", "x": 50, "y": 40},
                )
            ).status_code == 404
            assert site.submissions == []
            await wait_processes_gone(owned)
            await assert_viewer_tasks_settled()
        finally:
            site.release_slow.set()
            if not dispatch.done():
                dispatch.cancel()
            await asyncio.gather(dispatch, return_exceptions=True)


async def test_private_routes_require_authenticated_human_task_csrf_and_origin(
    app: Application, monkeypatch: pytest.MonkeyPatch
) -> None:
    import httpx2

    from core.authentication import CSRF_HEADER, OWNER_SESSION_COOKIE, OwnerSessionCodec
    from core.runtime.state import get_runtime_context
    from validation.support.browser.human_transport import claimed_browser
    from validation.support.runtime.authentication import (
        OWNER_SECRET,
        authentication_application,
        owner_policy,
    )

    async with claimed_browser(app, monkeypatch, session_id="private-route-auth") as (
        _site,
        _client,
        operation,
        path,
        state,
    ):
        application = authentication_application(
            app, "owner_token", ASSISTANTMD_AUTH_SECRET=OWNER_SECRET
        )
        issued = OwnerSessionCodec(owner_policy(app)).issue()
        app.evidence.register_secret(issued.cookie_value, issued.csrf_token)
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(
                app=application, client=("192.0.2.9", 50000)
            ),
            base_url="https://assistant.example.test",
        ) as client:
            assert (await client.get(path + "/frame")).status_code == 401
            client.cookies.set(OWNER_SESSION_COOKIE, issued.cookie_value)
            response = await client.get(path + "/frame")
            metadata = frame_metadata(response)
            assert response.headers["cross-origin-resource-policy"] == "same-origin"
            assert response.headers["x-content-type-options"] == "nosniff"
            payload = {**metadata, "kind": "click", "x": 40, "y": 40}
            assert (await client.post(path + "/input", json=payload)).status_code == 403
            headers = {
                CSRF_HEADER: issued.csrf_token,
                "Origin": "https://assistant.example.test",
            }
            assert (
                await client.post(
                    path + "/input",
                    json=payload,
                    headers={**headers, "Origin": "https://other.example.test"},
                )
            ).status_code == 403
            assert (
                await client.post(path + "/input", json=payload, headers=headers)
            ).status_code == 200
            metadata = frame_metadata(await client.get(path + "/frame"))
            await get_runtime_context().task_coordinator.cancel_task(state["task_id"])
            with pytest.raises(asyncio.CancelledError):
                await operation
            assert (await client.get(path + "/frame")).status_code == 409
            refused = await client.post(
                path + "/input",
                json={**metadata, "kind": "text", "text": "late"},
                headers=headers,
            )
            assert refused.status_code == 409
