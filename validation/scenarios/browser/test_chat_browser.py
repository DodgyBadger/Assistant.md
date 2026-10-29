"""Real chat submission, selection sends and streaming with controlled remote LLM input."""

import asyncio
import hashlib
import re
from pathlib import Path

import pytest
from playwright.async_api import expect

from validation.scenarios.support.browser_fullstack import (
    hold_delivery,
    lose_connection,
    read_file,
    select_passage,
    setting,
)
from validation.scenarios.support.chat_idle_provider import idle_provider
from validation.scenarios.support.session_ops_chat import configure_provider

pytestmark = pytest.mark.scenario


def configure_chat(application, provider):
    # Only the remote model is substituted: live models would add credentials,
    # cost and nondeterministic timing. The real agent, task stream and stores run.
    configure_provider(application.app, provider)
    setting(application.app, "default_model", "session-ops-model")
    response = application.app.request(
        "/api/system/models/session-ops-model",
        method="PUT",
        data={
            "provider": "session-ops-provider",
            "model_string": "session-ops-model",
            "capabilities": ["text", "vision"],
        },
    )
    assert response.status_code == 200, response.text


async def wait_model(provider):
    assert await asyncio.to_thread(
        provider.started.wait, 15
    ), "Real model request never arrived"


async def composer(ui, application):
    await ui.goto(application.url)
    await ui.locator("#vault-selector").select_option(application.vault.name)
    await ui.locator("#chat-input").fill("Unrelated existing draft")
    await ui.locator("#attach-input").set_input_files(
        Path("validation/fixtures/files/test_image.jpg")
    )


async def test_normal_chat_consumes_draft_and_image_and_persists_terminal_response(
    browser_app, ui
):
    with idle_provider() as provider:
        configure_chat(browser_app, provider)
        await composer(ui, browser_app)
        await ui.get_by_role("button", name="Send message", exact=True).click()
        await wait_model(provider)
        await expect(ui.locator("#chat-input")).to_have_value("")
        content = provider.requests[0]["messages"][-1]["content"]
        assert any(p.get("type") == "image_url" for p in content)
        assert any(p.get("text") == "Unrelated existing draft" for p in content)
        provider.release.set()
        await expect(
            ui.locator("#chat-messages").get_by_text("idle response", exact=True)
        ).to_be_visible(timeout=20000)
        await expect(
            ui.get_by_role("button", name="Fork session from this message", exact=True)
        ).to_have_count(1)
        await ui.reload()
        await ui.locator("#vault-selector").select_option(browser_app.vault.name)
        await ui.get_by_role("button", name="Open chat settings", exact=True).click()
        await ui.locator("[data-session-browser-row-id]").first.click()
        await expect(
            ui.locator("#chat-messages").get_by_text("idle response", exact=True)
        ).to_be_visible()


async def test_selection_send_acceptance_preserves_composer_and_continued_reading(
    browser_app, ui
):
    browser_app.app.write(
        browser_app.vault,
        "Reading.md",
        "# Reading\n\nSelected evidence.\n\nNext evidence.",
    )
    with idle_provider() as provider:
        configure_chat(browser_app, provider)
        await composer(ui, browser_app)
        reader = await read_file(ui, browser_app, "Reading.md")
        await select_passage(ui, reader, "Selected evidence.", "Explain this")
        async with hold_delivery(
            ui, browser_app.app, browser_app.url + "/api/chat/tasks", method="POST"
        ) as gate:
            await ui.get_by_role("button", name="Send now", exact=True).click()
            await asyncio.wait_for(gate.held.wait(), 10)
            await expect(
                ui.get_by_role("button", name="Send now", exact=True)
            ).to_be_disabled()
            await expect(ui.get_by_label("Instruction (optional)")).not_to_be_editable()
            await expect(ui.locator("#vault-selector")).to_be_disabled()
            await expect(
                ui.get_by_role("button", name="Discard selection prompt")
            ).to_be_disabled()
            await reader.get_by_role("button", name="Close", exact=True).click()
            await expect(reader).to_be_visible()
            assert len(gate.requests) == 1
            gate.release.set()
        await wait_model(provider)
        await expect(ui.get_by_label("Instruction (optional)")).to_be_hidden()
        await expect(ui.locator("#chat-input")).to_have_value(
            "Unrelated existing draft"
        )
        sent = str(provider.requests[0]["messages"][-1]["content"])
        assert "Selected evidence." in sent and "Explain this" in sent
        assert "Unrelated existing draft" not in sent and "image_url" not in sent
        await select_passage(ui, reader, "Next evidence.", "Next instruction")
        await expect(ui.get_by_label("Instruction (optional)")).to_be_focused()
        provider.release.set()
        await expect(ui.get_by_label("Instruction (optional)")).to_have_value(
            "Next instruction"
        )
        await expect(reader).to_be_visible()
        # The completed task must not steal focus or erase the next instruction.
        await expect(
            ui.locator("#chat-messages").get_by_text("idle response", exact=True)
        ).to_be_attached(timeout=20000)
        # Type without clicking or focusing anything: completion must leave the
        # user's editing target intact, even as history and file reads finish.
        await ui.keyboard.insert_text(" continued")
        await expect(ui.get_by_label("Instruction (optional)")).to_have_value(
            "Next instruction continued"
        )
        await ui.get_by_role("button", name="Discard selection prompt").click()
        await reader.get_by_role("button", name="Close", exact=True).click()
        if not await ui.get_by_role("button", name="Remove attachment").is_visible():
            await ui.get_by_role("button", name="Attach image(s)", exact=True).hover()
        await expect(ui.get_by_role("button", name="Remove attachment")).to_have_count(
            1
        )


async def test_failed_selection_start_preserves_instruction_and_can_retry(
    browser_app, ui
):
    browser_app.app.write(browser_app.vault, "Reading.md", "Selected evidence.")
    with idle_provider() as provider:
        configure_chat(browser_app, provider)
        reader = await read_file(ui, browser_app, "Reading.md")
        await select_passage(ui, reader, "Selected evidence.", "Keep this request")
        async with lose_connection(
            ui, browser_app.app, browser_app.url + "/api/chat/tasks", method="POST"
        ) as dropped:
            await ui.get_by_role("button", name="Send now", exact=True).click()
            await expect(
                ui.get_by_role("button", name="Send now", exact=True)
            ).to_be_enabled()
            await expect(ui.get_by_label("Instruction (optional)")).to_have_value(
                "Keep this request"
            )
            assert len(dropped) == 1
        await ui.get_by_role("button", name="Send now", exact=True).click()
        await wait_model(provider)
        provider.release.set()
        await expect(
            ui.locator("#chat-messages").get_by_text("idle response", exact=True)
        ).to_be_attached(timeout=20000)
        assert len(provider.requests) == 1


@pytest.mark.parametrize(
    "transition", ["stay", "navigate", "dismiss", "edit", "lost_refresh", "scroll"]
)
async def test_completed_selection_send_respects_current_reader_and_local_edits(
    browser_app, ui, transition
):
    content = "# Reading\n\nSelected evidence."
    if transition == "scroll":
        content = "\n\n".join(
            [
                *(f"Paragraph {i}." for i in range(75)),
                "Selected evidence.",
                *(f"Paragraph {i}." for i in range(76, 100)),
            ]
        )
    browser_app.app.write(browser_app.vault, "Reading.md", content)
    browser_app.app.write(browser_app.vault, "Next.md", "# Next\n\nOther evidence.")
    with idle_provider() as provider:
        configure_chat(browser_app, provider)
        reader = await read_file(ui, browser_app, "Reading.md")
        if transition == "scroll":
            await reader.locator("#vault-file-modal-preview p").filter(
                has_text="Selected evidence."
            ).scroll_into_view_if_needed()
        scroll_before = await reader.locator("#vault-file-modal-preview").evaluate(
            "element => element.scrollTop"
        )
        await select_passage(ui, reader, "Selected evidence.", "Read this")
        await ui.get_by_role("button", name="Send now", exact=True).click()
        await wait_model(provider)
        await expect(ui.get_by_label("Instruction (optional)")).to_be_hidden()
        if transition == "navigate":
            await reader.get_by_role("button", name="Back", exact=True).click()
            await ui.locator('[data-vault-path-picker-select="Next.md"]').click()
            await expect(reader.locator("#vault-file-modal-path")).to_have_text(
                "Next.md"
            )
        elif transition == "dismiss":
            await reader.get_by_role("button", name="Close", exact=True).click()
        path = "Next.md" if transition == "navigate" else "Reading.md"
        # This is a real public file edit while the remote model is held. It
        # isolates refresh behavior; the tool-write journey separately proves
        # that a real agent can cause the corresponding durable modification.
        response = await browser_app.app.request_async(
            f"/api/vaults/{browser_app.vault.name}/files",
            method="PUT",
            params={"path": path},
            data={
                "content": (
                    content.replace("Selected evidence.", "Durable server update.")
                    if transition == "scroll"
                    else "# Refreshed\n\nDurable server update."
                ),
                "expected_sha256": hashlib.sha256(
                    (browser_app.vault / path).read_bytes()
                ).hexdigest(),
            },
        )
        assert response.status_code == 200, response.text
        if transition == "dismiss":
            provider.release.set()
            await expect(
                ui.locator("#chat-messages").get_by_text("idle response", exact=True)
            ).to_be_attached(timeout=20000)
            await expect(reader).to_have_count(0)
        elif transition == "lost_refresh":
            async with lose_connection(
                ui,
                browser_app.app,
                browser_app.url
                + f"/api/vaults/{browser_app.vault.name}/files?path={path}",
            ) as dropped:
                provider.release.set()
                await expect(reader).to_contain_text("Could not refresh this file")
                assert len(dropped) == 1
                await expect(
                    reader.locator("#vault-file-modal-preview")
                ).to_contain_text("Selected evidence.")
        else:
            async with hold_delivery(
                ui,
                browser_app.app,
                browser_app.url
                + f"/api/vaults/{browser_app.vault.name}/files?path={path}",
            ) as gate:
                provider.release.set()
                await asyncio.wait_for(gate.held.wait(), 20)
                if transition == "edit":
                    await reader.get_by_role("button", name="Edit Markdown").click()
                    await reader.locator("#vault-file-modal-editor").fill(
                        "Unsaved local work"
                    )
                gate.release.set()
            if transition == "edit":
                await expect(reader.locator("#vault-file-modal-editor")).to_have_value(
                    "Unsaved local work"
                )
                assert (
                    "Durable server update." in (browser_app.vault / path).read_text()
                )
            else:
                await expect(
                    reader.locator("#vault-file-modal-preview")
                ).to_contain_text("Durable server update.")
                await expect(reader.locator("#vault-file-modal-path")).to_have_text(
                    path
                )
                if transition == "scroll":
                    assert scroll_before > 0
                    after = await reader.locator("#vault-file-modal-preview").evaluate(
                        "element => element.scrollTop"
                    )
                    assert abs(after - scroll_before) <= 2


async def test_real_tool_write_renders_one_turn_and_persisted_details(browser_app, ui):
    from validation.scenarios.support.code_tool_chat import code_tool_provider
    from validation.scenarios.support.code_tool_chat import (
        configure_provider as configure_code_provider,
    )

    # Substitute the remote model only. Its tool request is executed by the real
    # agent, Monty runtime and shared file mutation service; no app service mocks.
    with code_tool_provider() as provider:
        configure_code_provider(browser_app.app, provider)
        setting(browser_app.app, "default_model", "code-tool-model")
        provider.arguments = {
            "code": 'await file_write(operation="write", path="notes/derived.md", content="DERIVED_RESULT")\n"WRITE_OK"'
        }
        await ui.goto(browser_app.url)
        await ui.locator("#vault-selector").select_option(browser_app.vault.name)
        await ui.locator("#chat-input").fill("Write the derived note")
        await ui.get_by_role("button", name="Send message", exact=True).click()
        tool = ui.get_by_role("button", name=re.compile(r"^code_execution:"))
        await expect(
            ui.get_by_role("button", name="Fork session from this message", exact=True)
        ).to_have_count(1, timeout=30000)
        await expect(
            ui.get_by_role("button", name="Send message", exact=True)
        ).to_be_enabled(timeout=30000)
        await ui.locator("#chat-messages summary").filter(
            has_text="Tool calls (1)"
        ).click()
        assert (browser_app.vault / "notes/derived.md").read_text() == "DERIVED_RESULT"
        await tool.click()
        modal = ui.get_by_role("dialog").filter(
            has=ui.locator("#chat-tool-call-modal-title")
        )
        await expect(modal).to_contain_text("WRITE_OK")
        await ui.keyboard.press("Escape")
        await expect(modal).to_have_count(0)
        await expect(tool).to_be_focused()
        await ui.reload()
        await ui.locator("#vault-selector").select_option(browser_app.vault.name)
        await ui.get_by_role("button", name="Open chat settings", exact=True).click()
        await ui.locator("[data-session-browser-row-id]").first.click()
        await ui.locator("#chat-messages summary").filter(
            has_text="Tool calls (1)"
        ).click()
        await expect(tool).to_have_count(1)
        await expect(
            ui.get_by_role("button", name="Fork session from this message", exact=True)
        ).to_have_count(1)
        await tool.click()
        await expect(modal).to_contain_text("WRITE_OK")


async def test_older_file_refresh_cannot_unlock_a_new_selection_send(browser_app, ui):
    from validation.scenarios.support.browser_fullstack import hold_deliveries

    browser_app.app.write(
        browser_app.vault, "Reading.md", "First evidence.\n\nSecond evidence."
    )
    with idle_provider() as provider:
        configure_chat(browser_app, provider)
        reader = await read_file(ui, browser_app, "Reading.md")
        await select_passage(ui, reader, "First evidence.", "First request")
        await ui.get_by_role("button", name="Send now", exact=True).click()
        await wait_model(provider)
        await expect(ui.get_by_label("Instruction (optional)")).to_be_hidden()
        await select_passage(ui, reader, "Second evidence.", "Second request")
        async with hold_deliveries(
            ui,
            browser_app.app,
            (
                browser_app.url
                + f"/api/vaults/{browser_app.vault.name}/files?path=Reading.md",
                "GET",
            ),
            (browser_app.url + "/api/chat/tasks", "POST"),
        ) as (refresh, submission):
            provider.release.set()
            await asyncio.wait_for(refresh.held.wait(), 20)
            await ui.get_by_role("button", name="Send now", exact=True).click()
            await asyncio.wait_for(submission.held.wait(), 10)
            refresh.release.set()
            await expect(ui.get_by_label("Instruction (optional)")).not_to_be_editable()
            await expect(
                ui.get_by_role("button", name="Send now", exact=True)
            ).to_be_disabled()
            assert len(submission.requests) == 1
            submission.release.set()
        await expect(
            ui.locator("#chat-messages").get_by_text("idle response", exact=True)
        ).to_have_count(2, timeout=20000)


async def test_selection_send_declined_discard_preserves_real_unsaved_edits(
    browser_app, ui
):
    browser_app.app.write(browser_app.vault, "Reading.md", "Original evidence.")
    with idle_provider() as provider:
        configure_chat(browser_app, provider)
        reader = await read_file(ui, browser_app, "Reading.md")
        await reader.get_by_role("button", name="Edit Markdown", exact=True).click()
        await reader.locator("#vault-file-modal-editor").fill("Unsaved evidence.")
        await reader.get_by_role("button", name="Preview Markdown", exact=True).click()
        await select_passage(ui, reader, "Unsaved evidence.", "Explain this draft")
        ui.once("dialog", lambda dialog: dialog.dismiss())
        await ui.get_by_role("button", name="Send now", exact=True).click()
        await expect(ui.get_by_label("Instruction (optional)")).to_have_value(
            "Explain this draft"
        )
        assert not provider.requests
        assert (browser_app.vault / "Reading.md").read_text() == "Original evidence."
        ui.once("dialog", lambda dialog: dialog.accept())
        await ui.get_by_role("button", name="Send now", exact=True).click()
        await wait_model(provider)
        await expect(ui.get_by_label("Instruction (optional)")).to_be_hidden()
        assert "Unsaved evidence." in str(
            provider.requests[0]["messages"][-1]["content"]
        )
        provider.release.set()
        await expect(
            ui.locator("#chat-messages").get_by_text("idle response", exact=True)
        ).to_be_attached(timeout=20000)
        await expect(reader.locator("#vault-file-modal-preview")).to_contain_text(
            "Original evidence."
        )
        assert (browser_app.vault / "Reading.md").read_text() == "Original evidence."


async def test_real_chat_execution_failure_is_visible_and_releases_send_controls(
    browser_app, ui
):
    from validation.scenarios.support.browser_sessions import session_options

    browser_app.app.write(
        browser_app.vault,
        "AssistantMD/Authoring/broken-browser.md",
        '---\nrun_type: context\n---\n```python\nraise ValueError("Controlled browser authoring failure")\n```\n',
    )
    with idle_provider() as provider:
        configure_chat(browser_app, provider)
        dialog = await session_options(ui, browser_app)
        await ui.locator("#template-selector").select_option("broken-browser.md")
        await dialog.get_by_role(
            "button", name="Close chat settings", exact=True
        ).click()
        await ui.locator("#chat-input").fill("Run with the invalid context script")
        async with ui.expect_response(browser_app.url + "/api/chat/tasks") as started:
            await ui.get_by_role("button", name="Send message", exact=True).click()
        response = await started.value
        task_id = (await response.json())["task"]["task_id"]
        assert (await browser_app.app.wait_task(task_id))["status"] == "failed"
        await expect(ui.locator("#chat-messages")).to_contain_text(
            "Controlled browser authoring failure"
        )
        await expect(
            ui.get_by_role("button", name="Send message", exact=True)
        ).to_be_enabled()
        assert not provider.requests
