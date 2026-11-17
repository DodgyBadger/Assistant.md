"""Observation-bound actions operate on real controls without stale retargeting."""

from __future__ import annotations

import asyncio
import json

import pytest
from pydantic_ai.messages import ToolReturn

from validation.support.application import Application
from validation.support.browser.retained_site import browser_turn, retained_browser_site

pytestmark = pytest.mark.scenario

FORM = b"""<form method="post" action="/submit">
<label>Name<input name="name"></label>
<label>Colour<select name="colour"><option value="green">Green</option><option value="blue">Blue</option></select></label>
<button id="save">Save</button></form>"""


def element(view: dict, label: str) -> str:
    controls = view.get("elements")
    assert (
        isinstance(controls, list) and controls
    ), "A real page observation must expose actionable controls"
    matches = [item["ref"] for item in controls if item["label"] == label]
    assert len(matches) == 1, (label, controls)
    return matches[0]


def view(result: str | ToolReturn) -> dict:
    assert isinstance(result, str), "Browser action must return a new page observation"
    return json.loads(result)


async def configure(app: Application) -> None:
    await app.start()
    for key, value in (
        ("browser_min_memory_headroom_mb", "0"),
        ("browser_max_concurrent_sessions", "2"),
    ):
        assert (
            app.request(
                f"/api/system/settings/general/{key}", "PUT", {"value": value}
            ).status_code
            == 200
        )


async def test_browser_observes_fills_selects_and_submits_native_form(
    app: Application, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = app.vault("BrowserActionFormVault")
    await configure(app)
    with retained_browser_site(app, monkeypatch) as site:
        site.pages["/form"] = FORM
        try:
            first = view(
                await browser_turn(app, vault.name, "form-chat", url=site.url("/form"))
            )
            name_ref = element(first, "Name")
            filled = view(
                await browser_turn(
                    app,
                    vault.name,
                    "form-chat",
                    operation="fill",
                    reference=name_ref,
                    value="Ada",
                )
            )
            selected = view(
                await browser_turn(
                    app,
                    vault.name,
                    "form-chat",
                    operation="select",
                    reference=element(filled, "Colour"),
                    value="blue",
                )
            )
            saved = view(
                await browser_turn(
                    app,
                    vault.name,
                    "form-chat",
                    operation="click",
                    reference=element(selected, "Save"),
                )
            )
            assert site.submissions == [{"name": "Ada", "colour": "blue"}]
            assert "Saved" in saved["text"]
        finally:
            await browser_turn(app, vault.name, "form-chat", operation="close")


@pytest.mark.parametrize("replacement", ["read", "navigate", "browser", "other_chat"])
async def test_browser_rejects_old_or_foreign_element_references(
    app: Application, monkeypatch: pytest.MonkeyPatch, replacement: str
) -> None:
    vault = app.vault("BrowserStaleActionVault")
    await configure(app)
    with retained_browser_site(app, monkeypatch) as site:
        site.pages["/form"] = FORM
        try:
            first = view(
                await browser_turn(
                    app, vault.name, "reference-chat", url=site.url("/form")
                )
            )
            old = element(first, "Save")
            target = "reference-chat"
            if replacement == "browser":
                await browser_turn(app, vault.name, target, operation="close")
            elif replacement == "other_chat":
                target = "foreign-chat"
            fresh = view(
                await browser_turn(
                    app,
                    vault.name,
                    target,
                    **({} if replacement == "read" else {"url": site.url("/form")}),
                )
            )
            assert element(fresh, "Save") != old
            rejected = await browser_turn(
                app, vault.name, target, operation="click", reference=old
            )
            assert (
                isinstance(rejected, ToolReturn)
                and rejected.metadata["reason"] == "stale_reference"
            )
            assert site.submissions == []
        finally:
            for chat in ("reference-chat", "foreign-chat"):
                await browser_turn(app, vault.name, chat, operation="close")


async def test_browser_rejects_replaced_node_with_reused_dom_id(
    app: Application, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = app.vault("BrowserReplacedNodeVault")
    await configure(app)
    with retained_browser_site(app, monkeypatch) as site:
        site.pages["/changing"] = FORM + b"""<script>
fetch('/wait-mutation').then(() => {
const old = document.getElementById('save');
old.outerHTML = '<button id="save">Changed submission</button>';
fetch('/mutation-applied');
});</script>"""
        try:
            first = view(
                await browser_turn(
                    app, vault.name, "changed-chat", url=site.url("/changing")
                )
            )
            old = element(first, "Save")
            site.mutate.set()
            async with asyncio.timeout(5):
                while not site.mutation_applied.is_set():
                    await asyncio.sleep(0.02)
            rejected = await browser_turn(
                app, vault.name, "changed-chat", operation="click", reference=old
            )
            assert (
                isinstance(rejected, ToolReturn)
                and rejected.metadata["reason"] == "stale_reference"
            )
            assert site.submissions == []
        finally:
            site.mutate.set()
            await browser_turn(app, vault.name, "changed-chat", operation="close")


async def test_browser_scroll_uses_and_consumes_observation_reference(
    app: Application, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = app.vault("BrowserScrollActionVault")
    await configure(app)
    with retained_browser_site(app, monkeypatch) as site:
        site.pages["/scroll"] = (
            b'<button>Top</button><div style="height:1100px"></div><button>Bottom</button>'
        )
        try:
            first = view(
                await browser_turn(
                    app, vault.name, "scroll-chat", url=site.url("/scroll")
                )
            )
            assert not any(
                control["label"] == "Bottom" for control in first["elements"]
            )
            current = view(
                await browser_turn(
                    app,
                    vault.name,
                    "scroll-chat",
                    operation="scroll",
                    reference=first["observation_ref"],
                    scroll_y=600,
                )
            )
            element(current, "Bottom")
            rejected = await browser_turn(
                app,
                vault.name,
                "scroll-chat",
                operation="scroll",
                reference=first["observation_ref"],
                scroll_y=600,
            )
            assert (
                isinstance(rejected, ToolReturn)
                and rejected.metadata["reason"] == "stale_reference"
            )
        finally:
            await browser_turn(app, vault.name, "scroll-chat", operation="close")


async def test_browser_large_observation_is_bounded_and_omits_field_values(
    app: Application, monkeypatch: pytest.MonkeyPatch
) -> None:
    sentinel = "browser-value-must-stay-in-page-72591"
    app.evidence.register_secret(sentinel)
    vault = app.vault("BrowserBoundedObservationVault")
    await configure(app)
    with retained_browser_site(app, monkeypatch) as site:
        site.pages["/large"] = (
            "".join(
                f'<label>Field {index}<input value="{sentinel}" name="field{index}"></label>'
                for index in range(2500)
            )
            + f'<textarea>{sentinel}</textarea><input type="password" value="{sentinel}"><input autocomplete="one-time-code" value="{sentinel}">'
        ).encode()
        try:
            raw = await browser_turn(
                app, vault.name, "bounded-chat", url=site.url("/large")
            )
            current = view(raw)
            assert isinstance(raw, str) and len(raw) <= 48000
            assert 0 < len(current["elements"]) <= 60
            assert len(current["text"]) <= 12000 and current["truncated"]
            assert sentinel not in raw
        finally:
            await browser_turn(app, vault.name, "bounded-chat", operation="close")


@pytest.mark.parametrize(
    "mutation",
    [
        "credential",
        "layout",
        "meaning",
        "option",
        "target_form",
        "incoming_form",
        "detach_restore",
    ],
)
async def test_browser_rechecks_observed_control_before_dispatch(
    app: Application, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    sentinel = "normal-fill-value-should-not-appear-in-failure-83620"
    app.evidence.register_secret(sentinel)
    vault = app.vault("BrowserControlMutationVault")
    await configure(app)
    mutations = {
        "credential": "document.querySelector('input').type = 'password';",
        "layout": "document.querySelector('form').style.marginTop = '150px';",
        "meaning": "document.getElementById('save').textContent = 'Different submission';",
        "option": "document.querySelector('option').value = 'different';",
        "target_form": "document.getElementById('save').setAttribute('form', 'other');",
        "incoming_form": "document.getElementById('foreign').setAttribute('form', 'primary');",
        "detach_restore": "const button = document.getElementById('save'); const form = button.form; button.remove(); form.append(button);",
    }
    with retained_browser_site(app, monkeypatch) as site:
        site.pages["/mutate-control"] = (
            FORM.replace(b"<form ", b'<form id="primary" ', 1)
            + b'<form id="other" method="post" action="/submit"><input id="foreign" aria-label="Other" name="other"></form>'
            + (
                '<script>fetch("/wait-mutation").then(() => {'
                + mutations[mutation]
                + 'fetch("/mutation-applied");});</script>'
            ).encode()
        )
        try:
            first = view(
                await browser_turn(
                    app, vault.name, "mutated-control", url=site.url("/mutate-control")
                )
            )
            reference = element(first, "Name" if mutation == "credential" else "Save")
            site.mutate.set()
            async with asyncio.timeout(5):
                while not site.mutation_applied.is_set():
                    await asyncio.sleep(0.02)
            rejected = await browser_turn(
                app,
                vault.name,
                "mutated-control",
                operation="fill" if mutation == "credential" else "click",
                reference=reference,
                **({"value": sentinel} if mutation == "credential" else {}),
            )
            assert isinstance(rejected, ToolReturn) and rejected.metadata["reason"] in {
                "stale_reference",
                "sensitive_target",
            }
            assert site.submissions == []
            activity = (app.root / "system" / "activity.log").read_text()
            assert sentinel not in activity and sentinel not in str(rejected)
            assert sentinel not in json.dumps(app.events.select())
        finally:
            site.mutate.set()
            await browser_turn(app, vault.name, "mutated-control", operation="close")


async def test_consumed_element_reference_cannot_submit_twice(
    app: Application, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = app.vault("BrowserConsumedReferenceVault")
    await configure(app)
    with retained_browser_site(app, monkeypatch) as site:
        site.pages["/once"] = FORM
        try:
            first = view(
                await browser_turn(app, vault.name, "once-chat", url=site.url("/once"))
            )
            old = element(first, "Name")
            current = view(
                await browser_turn(
                    app,
                    vault.name,
                    "once-chat",
                    operation="fill",
                    reference=old,
                    value="Ada",
                )
            )
            assert current["observation_ref"] != first["observation_ref"]
            repeated = await browser_turn(
                app,
                vault.name,
                "once-chat",
                operation="fill",
                reference=old,
                value="Changed",
            )
            assert (
                isinstance(repeated, ToolReturn)
                and repeated.metadata["reason"] == "stale_reference"
            )
            saved = view(
                await browser_turn(
                    app,
                    vault.name,
                    "once-chat",
                    operation="click",
                    reference=element(current, "Save"),
                )
            )
            assert "Saved" in saved["text"]
            assert site.submissions == [{"name": "Ada", "colour": "green"}]
        finally:
            await browser_turn(app, vault.name, "once-chat", operation="close")


async def test_browser_enforces_serialized_budget_and_oversized_control_limits(
    app: Application, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = app.vault("BrowserOversizedObservationVault")
    await configure(app)
    with retained_browser_site(app, monkeypatch) as site:
        text = "漢\\\x01" * 10000
        controls = "".join(
            f'<button style="position:fixed;top:0">{("漢" + chr(1)) * 60}</button>'
            for _ in range(60)
        )
        site.pages["/oversized"] = (
            "<main>"
            + text
            + "</main>"
            + controls
            + '<label>Oversized<input name="'
            + "x" * 10000
            + '"></label><select aria-label="Oversized choices"><option value="'
            + "v" * 10000
            + '">Long</option></select>'
        ).encode()
        try:
            raw = await browser_turn(
                app, vault.name, "oversized-chat", url=site.url("/oversized")
            )
            current = view(raw)
            assert isinstance(raw, str) and len(raw) <= 48000
            assert len(current["elements"]) <= 60
            assert not any(
                item["label"] in {"Oversized", "Oversized choices"}
                for item in current["elements"]
            )
        finally:
            await browser_turn(app, vault.name, "oversized-chat", operation="close")


async def test_browser_select_refuses_option_disabled_through_group(
    app: Application, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = app.vault("BrowserDisabledOptionVault")
    await configure(app)
    with retained_browser_site(app, monkeypatch) as site:
        site.pages["/options"] = (
            b'<form method="post" action="/submit"><label>Choice<select name="choice"><option value="allowed">Allowed</option><optgroup disabled label="Unavailable"><option value="blocked">Blocked</option></optgroup></select></label><button>Save</button></form>'
        )
        try:
            first = view(
                await browser_turn(
                    app, vault.name, "options-chat", url=site.url("/options")
                )
            )
            rejected = await browser_turn(
                app,
                vault.name,
                "options-chat",
                operation="select",
                reference=element(first, "Choice"),
                value="blocked",
            )
            assert (
                isinstance(rejected, ToolReturn)
                and rejected.metadata["reason"] == "invalid_value"
            )
            fresh = view(await browser_turn(app, vault.name, "options-chat"))
            choice = next(
                control for control in fresh["elements"] if control["label"] == "Choice"
            )
            assert (
                next(option for option in choice["options"] if option["selected"])[
                    "value"
                ]
                == "allowed"
            )
            view(
                await browser_turn(
                    app,
                    vault.name,
                    "options-chat",
                    operation="click",
                    reference=element(fresh, "Save"),
                )
            )
            assert site.submissions == [{"choice": "allowed"}]
        finally:
            await browser_turn(app, vault.name, "options-chat", operation="close")


async def test_stop_during_browser_action_releases_lock_with_stalled_driver(
    app: Application, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    from core.runtime.execution_tasks import chat_session_scope
    from core.runtime.state import get_runtime_context
    from validation.support.browser.browser_runtime import descendant_identities
    from validation.support.browser.retained_site import (
        pause_driver_on_browser_event,
        wait_processes_gone,
    )

    vault = app.vault("BrowserStalledActionVault")
    await configure(app)
    with retained_browser_site(app, monkeypatch) as site:
        site.pages["/form"] = FORM
        baseline = {record["pid"] for record in descendant_identities()}
        first = view(
            await browser_turn(app, vault.name, "stalled-action", url=site.url("/form"))
        )
        owned = [
            record
            for record in descendant_identities()
            if record["pid"] not in baseline
        ]
        drivers = [record for record in owned if record["parent"] == os.getpid()]
        assert len(drivers) == 1
        with pause_driver_on_browser_event(
            app, drivers[0]["pid"], "browser_action_started"
        ) as paused:
            operation = asyncio.create_task(
                browser_turn(
                    app,
                    vault.name,
                    "stalled-action",
                    operation="click",
                    reference=element(first, "Save"),
                )
            )
            try:
                async with asyncio.timeout(5):
                    await paused.ready.wait()
                runtime = get_runtime_context()
                tasks = await runtime.task_coordinator.list_tasks(
                    scope=chat_session_scope("stalled-action"), include_terminal=False
                )
                assert len(tasks) == 1
                await runtime.task_coordinator.cancel_task(tasks[0].task_id)
                # Keep the actual IPC peer stopped throughout the assertion.
                # Cleanup's own deadlines must finish despite that condition.
                done, _ = await asyncio.wait({operation}, timeout=18)
                assert (
                    operation in done
                ), "Stop must release browser action ownership even when the driver never replies"
                with pytest.raises(asyncio.CancelledError):
                    await operation
                terminal = await runtime.task_coordinator.get_task(tasks[0].task_id)
                assert terminal is not None and terminal.status == "cancelled"
                assert site.submissions == []
            finally:
                paused.resume()
                await asyncio.gather(operation, return_exceptions=True)
        await wait_processes_gone(owned)


async def test_browser_native_actions_ignore_page_prototype_and_focus_redirection(
    app: Application, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = app.vault("BrowserIsolatedActionVault")
    await configure(app)
    with retained_browser_site(app, monkeypatch) as site:
        site.pages["/isolated"] = b"""<form method="post" action="/submit">
<label>Name<input name="name" onfocus="document.getElementById('credential').focus()"></label>
<label>Password<input type="password" id="credential" name="credential"></label>
<button>Save</button></form><script>
HTMLElement.prototype.click = function() { throw new Error('website replacement'); };
Object.defineProperty(HTMLInputElement.prototype, 'value', {set() { throw new Error('website setter'); }});
</script>"""
        try:
            first = view(
                await browser_turn(
                    app, vault.name, "isolated-chat", url=site.url("/isolated")
                )
            )
            assert not any(
                control["label"] == "Password" for control in first["elements"]
            )
            filled = view(
                await browser_turn(
                    app,
                    vault.name,
                    "isolated-chat",
                    operation="fill",
                    reference=element(first, "Name"),
                    value="Ada",
                )
            )
            view(
                await browser_turn(
                    app,
                    vault.name,
                    "isolated-chat",
                    operation="click",
                    reference=element(filled, "Save"),
                )
            )
            assert site.submissions == [{"name": "Ada"}]
        finally:
            await browser_turn(app, vault.name, "isolated-chat", operation="close")


async def test_browser_unicode_truncation_remains_utf8_serializable(
    app: Application, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = app.vault("BrowserUnicodeObservationVault")
    await configure(app)
    with retained_browser_site(app, monkeypatch) as site:
        label = "a" + "🚀" * 60
        text = "a" + "🚀" * 6000
        site.pages["/unicode"] = (
            f'<button aria-label="{label}">Unicode</button>'
            '<select aria-label="Malformed value"><option id="bad-value">Bad</option></select>'
            '<select aria-label="Valid choice"><option id="bad-label" value="safe">Label</option></select>'
            '<script>document.getElementById("bad-value").value = String.fromCharCode(0xd800);'
            'document.getElementById("bad-label").textContent = String.fromCharCode(0xd800);</script>'
            f"<p>{text}</p>"
        ).encode()
        try:
            raw = await browser_turn(
                app, vault.name, "unicode-chat", url=site.url("/unicode")
            )
            current = view(raw)
            assert isinstance(raw, str) and raw.encode("utf-8")
            assert current["elements"] and current["elements"][0]["label"].encode(
                "utf-8"
            )
            assert len(raw) <= 48000
            assert not any(
                item["label"] == "Malformed value" for item in current["elements"]
            )
            valid = next(
                item for item in current["elements"] if item["label"] == "Valid choice"
            )
            assert valid["options"][0]["value"] == "safe"
            assert valid["options"][0]["label"].encode("utf-8")
        finally:
            await browser_turn(app, vault.name, "unicode-chat", operation="close")


async def test_browser_owns_and_reaps_driver_stalled_before_initialization(
    app: Application, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os
    import signal

    from playwright._impl._driver import compute_driver_executable

    from validation.support.browser.browser_runtime import descendant_identities
    from validation.support.browser.retained_site import wait_processes_gone

    vault = app.vault("BrowserPartialStartupVault")
    await configure(app)
    original = asyncio.create_subprocess_exec
    ready = asyncio.Event()
    paused_pid = None
    driver, cli = compute_driver_executable()

    async def launch(*args, **kwargs):
        nonlocal paused_pid
        process = await original(*args, **kwargs)
        if tuple(map(str, args)) == (driver, cli, "run-driver") and paused_pid is None:
            # External process-boundary fault injection: the real SDK process
            # exists, but cannot consume its first initialization IPC message.
            paused_pid = process.pid
            os.kill(paused_pid, signal.SIGSTOP)
            ready.set()
        return process

    with retained_browser_site(app, monkeypatch) as site:
        with monkeypatch.context() as scope:
            scope.setattr(asyncio, "create_subprocess_exec", launch)
            operation = asyncio.create_task(
                browser_turn(app, vault.name, "partial-start", url=site.url("/state"))
            )
            try:
                async with asyncio.timeout(5):
                    await ready.wait()
                owned = [
                    record
                    for record in descendant_identities()
                    if record["pid"] == paused_pid
                ]
                assert len(owned) == 1
                done, _ = await asyncio.wait({operation}, timeout=12)
                assert (
                    operation in done
                ), "Partial driver initialization must have an owned deadline and cleanup"
                failed = await operation
                assert (
                    isinstance(failed, ToolReturn)
                    and failed.metadata["status"] == "failed"
                )

                await wait_processes_gone(owned)
                fresh = view(
                    await browser_turn(
                        app, vault.name, "partial-start", url=site.url("/state")
                    )
                )
                assert fresh["text"] == "Signed out"
            finally:
                if paused_pid is not None:
                    try:
                        os.kill(paused_pid, signal.SIGCONT)
                    except ProcessLookupError:
                        pass
                await asyncio.gather(operation, return_exceptions=True)
                await browser_turn(app, vault.name, "partial-start", operation="close")


@pytest.mark.parametrize("mutation", ["label", "value", "removed"])
async def test_browser_can_use_stable_form_while_unrelated_observed_controls_change(
    app: Application, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    vault = app.vault("BrowserUnrelatedMutationVault")
    await configure(app)
    changes = {
        "label": "document.getElementById('unrelated').textContent = 'Cart updated';",
        "value": "document.getElementById('unrelated').value = 'updated';",
        "removed": "document.getElementById('unrelated').remove();",
    }
    with retained_browser_site(app, monkeypatch) as site:
        site.pages["/live-form"] = (
            FORM
            + b'<button id="unrelated">Cart</button>'
            + (
                '<script>fetch("/wait-mutation").then(() => {'
                + changes[mutation]
                + 'fetch("/mutation-applied");});</script>'
            ).encode()
        )
        try:
            initial = view(
                await browser_turn(
                    app, vault.name, "live-form", url=site.url("/live-form")
                )
            )
            site.mutate.set()
            async with asyncio.timeout(5):
                while not site.mutation_applied.is_set():
                    await asyncio.sleep(0.02)
            filled = view(
                await browser_turn(
                    app,
                    vault.name,
                    "live-form",
                    operation="fill",
                    reference=element(initial, "Name"),
                    value="ordinary search value",
                )
            )
            submitted = view(
                await browser_turn(
                    app,
                    vault.name,
                    "live-form",
                    operation="click",
                    reference=element(filled, "Save"),
                )
            )
            assert "Saved" in submitted["text"]
            assert site.submissions == [
                {"name": "ordinary search value", "colour": "green"}
            ]
        finally:
            site.mutate.set()
            await browser_turn(app, vault.name, "live-form", operation="close")


async def test_browser_unowned_field_ignores_unrelated_unowned_control_mutation(
    app: Application, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = app.vault("BrowserUnownedFieldVault")
    await configure(app)
    with retained_browser_site(app, monkeypatch) as site:
        site.pages["/unowned-field"] = b"""<label>Search<input id="search"></label>
<output id="echo"></output><button id="unrelated">Cart</button><script>
document.getElementById('search').addEventListener('input', event => {
    document.getElementById('echo').textContent = event.target.value;
});
fetch('/wait-mutation').then(() => {
    document.getElementById('unrelated').textContent = 'Cart updated';
    fetch('/mutation-applied');
});</script>"""
        try:
            initial = view(
                await browser_turn(
                    app, vault.name, "unowned", url=site.url("/unowned-field")
                )
            )
            site.mutate.set()
            async with asyncio.timeout(5):
                while not site.mutation_applied.is_set():
                    await asyncio.sleep(0.02)
            result = view(
                await browser_turn(
                    app,
                    vault.name,
                    "unowned",
                    operation="fill",
                    reference=element(initial, "Search"),
                    value="ordinary query",
                )
            )
            assert "ordinary query" in result["text"]
            assert site.submissions == []
        finally:
            site.mutate.set()
            await browser_turn(app, vault.name, "unowned", operation="close")
