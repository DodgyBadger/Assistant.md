"""Observation-bound actions operate on real controls without stale retargeting."""

from __future__ import annotations

import asyncio
import json

import pytest
from pydantic_ai.messages import ToolReturn

from validation.support.application import Application
from validation.support.browser.actions import FORM, configure, element, view
from validation.support.browser.retained_site import browser_turn, retained_browser_site

pytestmark = pytest.mark.scenario


async def test_browser_observes_fills_selects_and_submits_native_form(
    app: Application, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = app.vault("BrowserActionFormVault")
    await configure(app)
    with retained_browser_site(app, monkeypatch) as site:
        site.pages["/form"] = FORM.replace(
            b"</select>",
            b'<optgroup disabled label="Unavailable"><option value="blocked">Blocked</option></optgroup></select>',
        )
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
            assert filled["observation_ref"] != first["observation_ref"]
            stale = await browser_turn(
                app,
                vault.name,
                "form-chat",
                operation="fill",
                reference=name_ref,
                value="Changed",
            )
            assert (
                isinstance(stale, ToolReturn)
                and stale.metadata["reason"] == "stale_reference"
            )
            rejected = await browser_turn(
                app,
                vault.name,
                "form-chat",
                operation="select",
                reference=element(filled, "Colour"),
                value="blocked",
            )
            assert (
                isinstance(rejected, ToolReturn)
                and rejected.metadata["reason"] == "invalid_value"
            )
            filled = view(await browser_turn(app, vault.name, "form-chat"))
            choice = next(
                control
                for control in filled["elements"]
                if control["label"] == "Colour"
            )
            assert (
                next(option for option in choice["options"] if option["selected"])[
                    "value"
                ]
                == "green"
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
            save_ref = element(selected, "Save")
            saved = view(
                await browser_turn(
                    app,
                    vault.name,
                    "form-chat",
                    operation="click",
                    reference=save_ref,
                )
            )
            assert site.submissions == [{"name": "Ada", "colour": "blue"}]
            assert "Saved" in saved["text"]
            repeated = await browser_turn(
                app, vault.name, "form-chat", operation="click", reference=save_ref
            )
            assert (
                isinstance(repeated, ToolReturn)
                and repeated.metadata["reason"] == "stale_reference"
            )
            assert site.submissions == [{"name": "Ada", "colour": "blue"}]
        finally:
            await browser_turn(app, vault.name, "form-chat", operation="close")


async def test_browser_rejects_old_or_foreign_element_references(
    app: Application, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = app.vault("BrowserStaleActionVault")
    await configure(app)
    with retained_browser_site(app, monkeypatch) as site:
        site.pages["/form"] = FORM
        for replacement in ("read", "navigate", "browser", "other_chat"):
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


async def test_browser_can_use_stable_form_while_unrelated_observed_controls_change(
    app: Application, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = app.vault("BrowserUnrelatedMutationVault")
    await configure(app)
    with retained_browser_site(app, monkeypatch) as site:
        site.pages["/live-form"] = (
            FORM
            + b'<button id="unrelated">Cart</button><input id="external"><button id="removed">Remove</button>'
            + (
                '<script>fetch("/wait-mutation").then(() => {'
                + "document.getElementById('unrelated').textContent='Cart updated';document.getElementById('external').value='updated';document.getElementById('removed').remove();"
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


async def test_browser_observation_bounds_exclude_values_and_serialize_unicode(
    app, monkeypatch
):
    sentinel = "browser-value-must-stay-in-page-72591"
    app.evidence.register_secret(sentinel)
    vault = app.vault("BrowserBoundedObservationVault")
    await configure(app)
    with retained_browser_site(app, monkeypatch) as site:
        site.pages["/large"] = (
            "<style>input,select,button{position:fixed;top:0}</style>"
            # Keep escaping pressure before the many labels so the text scan
            # cannot fill its budget before reaching this adversarial input.
            + "<main>"
            + "\x01" * 10000
            + "a"
            + "🚀" * 6000
            + "</main>"
            + '<button aria-label="a'
            + "🚀" * 60
            + '">Unicode</button>'
            + '<select aria-label="Malformed value"><option id="bad-value">Bad</option></select>'
            + '<select aria-label="Valid choice"><option id="bad-label" value="safe">Label</option></select>'
            + '<label>Oversized<input name="'
            + "x" * 10000
            + '"></label>'
            + '<select aria-label="Oversized choices"><option value="'
            + "v" * 10000
            + '">Long</option></select>'
            + "".join(
                f'<label>Field {i}<input value="{sentinel}" name="field{i}"></label>'
                for i in range(2500)
            )
            + f'<textarea>{sentinel}</textarea><input type="password" value="{sentinel}"><input autocomplete="one-time-code" value="{sentinel}">'
            + '<script>document.getElementById("bad-value").value=String.fromCharCode(0xd800);document.getElementById("bad-label").textContent=String.fromCharCode(0xd800);</script>'
        ).encode()
        try:
            raw = await browser_turn(
                app, vault.name, "bounded-chat", url=site.url("/large")
            )
            current = view(raw)
            assert isinstance(raw, str) and raw.encode("utf-8") and len(raw) <= 48000
            assert 0 < len(current["elements"]) <= 60
            assert len(current["text"]) <= 12000 and current["truncated"]
            assert any(item["tag"] == "input" for item in current["elements"])
            assert sentinel not in raw
            assert not any(
                item["label"] in {"Oversized", "Oversized choices", "Malformed value"}
                for item in current["elements"]
            )
            assert current["elements"][0]["label"].encode("utf-8")
            valid = next(
                item for item in current["elements"] if item["label"] == "Valid choice"
            )
            assert valid["options"][0]["value"] == "safe"
            assert valid["options"][0]["label"].encode("utf-8")
        finally:
            await browser_turn(app, vault.name, "bounded-chat", operation="close")
