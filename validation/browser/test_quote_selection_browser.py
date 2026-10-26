"""Real-browser checks for selection capture and draft/task ownership boundaries."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect, sync_playwright

_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def quote_page() -> Iterator[Page]:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=["--disable-gpu"])
        page = browser.new_page(viewport={"width": 1000, "height": 800}, has_touch=True)
        page.set_content('<textarea id="chat-input">Existing draft</textarea>')
        page.add_style_tag(path=str(_ROOT / "static/output.css"))
        page.add_style_tag(path=str(_ROOT / "static/app.css"))
        for module in (
            "icons",
            "file-reference-links",
            "vault-selection-prompt",
            "file-references",
            "chat-composer",
            "chat-task-actions",
        ):
            page.add_script_tag(path=str(_ROOT / f"static/js/{module}.js"))
        page.evaluate(r"""async () => {
            window.state = { sessionId: '', isLoading: false };
            window.compose = { pendingAttachments: [{ file: new File(['image'], 'keep.png', { type: 'image/png' }) }] };
            window.elements = { chatInput: document.querySelector('textarea'),
                vaultSelector: { value: 'Vault' }, modelSelector: { value: 'test' },
                sendBtn: { disabled: false } };
            window.fileContents = {}; window.fileReads = []; window.failFileReads = false;
            window.sent = []; window.failStart = false; window.holdStart = false; window.closedPicker = false;
            window.fetch = async (url, options) => {
                if (url === 'api/chat/tasks') {
                    sent.push(options.body instanceof FormData
                        ? { prompt: options.body.get('prompt'), images: options.body.getAll('images').map(file => file.name) }
                        : JSON.parse(options.body));
                    if (holdStart) await new Promise(resolve => { window.releaseStart = resolve; });
                    return failStart ? { ok: false, json: async () => ({ message: 'Start failed' }) }
                        : { ok: true, json: async () => ({ session_id: 'session-new', task: { task_id: 'task' } }) };
                }
                const filePath = new URL(url, 'http://test/').searchParams.get('path');
                fileReads.push(filePath);
                if (failFileReads) return { ok: false, status: 503, json: async () => ({message:'Refresh unavailable'}) };
                const content = fileContents[filePath] || 'fixture';
                return { ok: true, json: async () => ({ content, sha256: content }) };
            };
            window.composer = ChatComposer.create({ state, composeState: compose, elements,
                utils: {}, browserStorage: {} });
            const noop = () => {};
            const sessionControls = { renderSelector: noop, updateTitleRow: noop, refreshCompactionState: noop };
            window.actions = ChatTaskActions.create({ state, composeState: compose, elements,
                chatRendering: { addMessage: noop, addLoadingMessage: noop, removeLoadingMessage: noop,
                    addErrorMessage: noop, createAssistantStreamingMessage: () => ({}),
                    finalizeAssistantMessage: noop, getActiveToolDetailId: noop },
                chatTaskStream: { consumeEvents: () => new Promise(resolve => { window.finishStream = resolve; }),
                    releaseActiveStream: () => { state.isLoading = false; return true; } },
                sessionControls, workspacePicker: { currentPath: () => '' },
                callbacks: { syncChatControlLocks: () => window.files?.syncInteractionLocks(),
                    createClientSessionId: () => 'session-new', clearPendingAttachments: () => { compose.pendingAttachments = []; },
                    fetchSessions: async () => {}, loadSession: async () => {} } });
            window.files = FileReferences.create({ state, elements, icons: AssistantMDIcons,
                utils: { escapeHtml: value => String(value).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;') },
                callbacks: { isInteractionLocked: () => state.isLoading || Boolean(state.pendingDeferredReview),
                    appendPromptBlock: block => composer.appendPromptBlock(block),
                    sendSelectionPrompt: (block, options) => actions.sendMessage(block, { ...options, includeAttachments: false, focusComposerOnFinish: false }),
                    openPathPicker: options => {
                        window.pickerOptions = options;
                        const tree = document.createElement('div');
                        tree.id = 'vault-explorer-modal';
                        document.body.appendChild(tree);
                    },
                    closePathPicker: () => {
                        closedPicker = true;
                        document.querySelector('#vault-explorer-modal')?.remove();
                    },
                    renderMarkdownPreview: (container, content) => {
                        if (content !== 'fixture') {
                            container.textContent = content;
                            if (content.startsWith('Long update')) {
                                const tail = document.createElement('p');
                                tail.style.height = '1200px';
                                container.appendChild(tail);
                            }
                            return;
                        }
                        container.innerHTML = '<p id="passage">First passage.</p><p id="other">Another passage.</p>'
                            + '<p style="height:1200px">More document text</p>';
                    } } });
            window.selectPassage = (selector = '#passage') => {
                const range = document.createRange(); range.selectNodeContents(document.querySelector(selector));
                const selection = getSelection(); selection.removeAllRanges(); selection.addRange(range);
            };
            await files.openFile('Notes/A.md');
        }""")
        yield page
        browser.close()


def open_prompt(page: Page, text: str = "Instruction") -> None:
    page.evaluate("selectPassage()")
    page.locator("[data-quote-open]").click()
    page.locator("#vault-selection-input").fill(text)


def test_append_repeated_quotes_and_keep_file_open(quote_page: Page) -> None:
    page = quote_page
    open_prompt(page, "First instruction\nSecond line")
    page.locator("[data-quote-append]").click()
    assert page.locator("#chat-input").input_value() == (
        "Existing draft\n\n[[Notes/A.md]]\n\n> First passage.\n\n"
        "First instruction\nSecond line"
    )
    assert page.locator("#vault-file-modal").count() == 1
    page.evaluate("selectPassage('#other')")
    page.locator("[data-quote-open]").click()
    page.locator("[data-quote-append]").click()
    assert (
        page.locator("#chat-input")
        .input_value()
        .endswith("[[Notes/A.md]]\n\n> Another passage.")
    )
    assert page.evaluate("compose.pendingAttachments.length") == 1
    assert page.evaluate("sent.length") == 0


def test_send_acceptance_preserves_draft_and_images_before_stream_finishes(
    quote_page: Page,
) -> None:
    page = quote_page
    open_prompt(page)
    page.locator("#vault-selection-input").press("Control+Enter")
    page.wait_for_function(
        "sent.length === 1 && document.querySelector('#vault-selection-panel').hidden"
    )
    assert (
        page.evaluate("sent[0].prompt")
        == "[[Notes/A.md]]\n\n> First passage.\n\nInstruction"
    )
    assert page.locator("#chat-input").input_value() == "Existing draft"
    assert page.evaluate("compose.pendingAttachments.length") == 1
    assert page.evaluate("!closedPicker && state.isLoading")
    assert page.locator("#vault-file-modal").count() == 1


def test_failed_start_retains_entry_and_new_session_can_retry(quote_page: Page) -> None:
    page = quote_page
    page.evaluate("failStart = true")
    open_prompt(page)
    page.locator("[data-quote-send]").click()
    page.wait_for_function("!state.isLoading && sent.length === 1")
    assert page.locator("#vault-selection-input").input_value() == "Instruction"
    assert "Start failed" in page.locator(".vault-selection-feedback").inner_text()
    assert page.locator("[data-quote-send]").is_enabled()
    page.evaluate("failStart = false")
    page.locator("[data-quote-send]").click()
    page.wait_for_function(
        "sent.length === 2 && document.querySelector('#vault-selection-panel').hidden"
    )
    assert page.evaluate("compose.pendingAttachments.length") == 1


def test_context_locks_dismissal_and_navigation(quote_page: Page) -> None:
    page = quote_page
    open_prompt(page)
    page.locator("#vault-selection-input").press("Escape")
    assert page.locator("#vault-file-modal").count() == 1
    page.locator("[data-quote-open]").click()
    assert page.locator("#vault-selection-input").input_value() == "Instruction"
    page.evaluate("state.pendingDeferredReview = {}; files.syncInteractionLocks()")
    assert page.locator("[data-quote-send]").is_disabled()
    assert page.locator("[data-quote-append]").is_disabled()
    page.evaluate("state.pendingDeferredReview = null; files.syncInteractionLocks()")
    assert page.locator("[data-quote-send]").is_enabled()
    page.evaluate("state.sessionId = 'elsewhere'; files.syncInteractionLocks()")
    assert page.locator("[data-quote-append]").is_disabled()
    page.evaluate("state.sessionId = ''; files.syncInteractionLocks()")
    page.once("dialog", lambda dialog: dialog.dismiss())
    page.locator('[data-vault-file-mode="edit"]').click()
    assert page.locator("#vault-selection-input").is_visible()
    page.once("dialog", lambda dialog: dialog.dismiss())
    page.locator('[data-vault-file-close="true"]').last.click()
    assert page.locator("#vault-file-modal").count() == 1
    page.once("dialog", lambda dialog: dialog.accept())
    page.locator('[data-vault-file-mode="edit"]').click()
    assert page.locator("#vault-selection-input").is_hidden()
    page.locator('[data-vault-file-close="true"]').last.click()
    assert page.locator(".vault-selection-prompt").count() == 0


def test_mobile_layout_and_selection_isolation(quote_page: Page) -> None:
    page = quote_page
    page.set_viewport_size({"width": 390, "height": 640})
    page.evaluate("selectPassage()")
    page.locator("[data-quote-open]").tap()
    page.locator("#vault-selection-input").fill("A multiline\ninstruction")
    page.wait_for_timeout(200)  # Let the width transition settle before measuring.
    box = page.locator(".vault-selection-prompt").bounding_box()
    assert box and box["x"] >= 0 and box["x"] + box["width"] <= 390
    assert box["y"] >= 0 and box["y"] + box["height"] <= 640
    page.locator("[data-quote-dismiss]").click()
    page.evaluate("""() => {
        const range = document.createRange(); range.selectNodeContents(document.querySelector('#vault-file-modal-title'));
        getSelection().removeAllRanges(); getSelection().addRange(range);
    }""")
    assert page.locator("[data-quote-open]").is_hidden()


def test_mouse_selection_preserves_quote_while_typing_and_collects_across_files(
    quote_page,
):
    page = quote_page
    page.locator("#passage").dblclick(position={"x": 10, "y": 8})
    page.locator("[data-quote-open]").click()
    original = page.locator("[data-quote-text]").inner_text()
    assert original.strip()
    page.locator("#vault-selection-input").fill("Keep this instruction")
    page.evaluate("selectPassage('#other')")
    assert page.locator("[data-quote-text]").inner_text() == original
    page.locator("[data-quote-append]").click()
    page.evaluate("files.openFile('Notes/B.md')")
    open_prompt(page, "Second file")
    page.locator("[data-quote-append]").click()
    draft = page.locator("#chat-input").input_value()
    assert draft.index("[[Notes/A.md]]") < draft.index("[[Notes/B.md]]")
    assert "Keep this instruction" in draft and "Second file" in draft


def test_normal_chat_send_still_consumes_its_draft_and_images(quote_page: Page) -> None:
    page = quote_page
    page.locator('[data-vault-file-close="true"]').last.click()
    page.evaluate("void actions.sendMessage()")
    page.wait_for_function("sent.length === 1 && typeof finishStream === 'function'")
    assert page.evaluate("sent[0]") == {
        "prompt": "Existing draft",
        "images": ["keep.png"],
    }
    assert page.locator("#chat-input").input_value() == ""
    assert page.evaluate("compose.pendingAttachments.length") == 0


def test_starting_send_locks_entry_and_prevents_duplicate_submission(
    quote_page: Page,
) -> None:
    page = quote_page
    open_prompt(page, "Keep until accepted")
    page.evaluate("holdStart = true")
    page.locator("[data-quote-send]").click()
    page.wait_for_function("typeof releaseStart === 'function'")
    assert page.locator("#vault-selection-input").get_attribute("readonly") is not None
    assert page.locator("[data-quote-dismiss]").is_disabled()
    page.locator("#vault-selection-input").press("Control+Enter")
    assert page.evaluate("sent.length") == 1
    page.evaluate("failStart = true; releaseStart()")
    page.wait_for_function("!state.isLoading")
    assert page.locator("#vault-selection-input").input_value() == "Keep until accepted"
    expect(page.locator("#vault-selection-input")).to_be_focused()
    assert page.locator("[data-quote-dismiss]").is_enabled()


def test_append_retains_scroll_and_short_viewport_stays_bounded(
    quote_page: Page,
) -> None:
    page = quote_page
    page.evaluate("document.querySelector('#vault-file-modal-preview').scrollTop = 200")
    scroll = page.locator("#vault-file-modal-preview").evaluate(
        "node => node.scrollTop"
    )
    open_prompt(page)
    page.set_viewport_size({"width": 390, "height": 320})
    page.wait_for_timeout(200)
    box = page.locator(".vault-selection-prompt").bounding_box()
    assert box and box["y"] >= 0 and box["y"] + box["height"] <= 320
    page.locator("[data-quote-append]").click()
    assert (
        page.locator("#vault-file-modal-preview").evaluate("node => node.scrollTop")
        == scroll
    )


def test_send_does_not_silently_discard_unsaved_file_edits(quote_page: Page) -> None:
    page = quote_page
    open_prompt(page)
    page.evaluate(
        "document.querySelector('#vault-file-modal-editor').value = 'Unsaved changes'"
    )
    page.once("dialog", lambda dialog: dialog.dismiss())
    page.locator("[data-quote-send]").click()
    assert page.evaluate("sent.length") == 0
    assert page.locator("#vault-selection-input").input_value() == "Instruction"
    assert page.locator("#vault-file-modal-editor").input_value() == "Unsaved changes"
    page.once("dialog", lambda dialog: dialog.accept())
    page.locator("[data-quote-send]").click()
    page.wait_for_function(
        "sent.length === 1 && document.querySelector('#vault-selection-panel').hidden"
    )


def test_keyboard_activation_and_enter_adds_newline(quote_page: Page) -> None:
    page = quote_page
    page.evaluate("selectPassage()")
    page.locator("#vault-file-modal-preview").focus()
    page.keyboard.press("Tab")
    expect(page.locator("[data-quote-open]")).to_be_focused()
    page.keyboard.press("Enter")
    expect(page.locator("#vault-selection-input")).to_be_focused()
    page.keyboard.type("First line")
    page.keyboard.press("Enter")
    page.keyboard.type("Second line")
    assert (
        page.locator("#vault-selection-input").input_value()
        == "First line\nSecond line"
    )
    assert page.evaluate("sent.length") == 0


@pytest.mark.parametrize("dismiss", ["close", "backdrop"])
def test_explicit_dismissal_exits_tree_and_nested_files(
    quote_page: Page, dismiss: str
) -> None:
    page = quote_page
    page.evaluate("files.openExplorer()")
    page.evaluate("pickerOptions.onOpenFile('Notes/A.md')")
    page.evaluate("files.openFile('Notes/B.md')")
    page.evaluate("files.openFile('Notes/C.md')")
    if dismiss == "close":
        page.locator('[data-vault-file-close="true"]').last.click()
    else:
        page.locator('#vault-file-modal > [data-vault-file-close="true"]').click(
            position={"x": 20, "y": 400}
        )
    assert page.locator("#vault-file-modal").count() == 0
    assert page.locator("#vault-explorer-modal").count() == 0
    assert not page.evaluate("files.isPickerOpen()")
    expect(page.locator("#chat-input")).to_be_focused()
    page.evaluate("files.openFile('Notes/Fresh.md')")
    assert page.locator('[data-vault-file-back="true"]').count() == 0


def test_back_keeps_tree_and_explicit_close_preserves_declined_changes(
    quote_page: Page,
) -> None:
    page = quote_page
    page.evaluate("files.openExplorer()")
    page.evaluate("pickerOptions.onOpenFile('Notes/A.md')")
    page.evaluate("files.openFile('Notes/B.md')")
    page.locator('[data-vault-file-back="true"]').click()
    expect(page.locator("#vault-file-modal-path")).to_have_text("Notes/A.md")
    assert page.locator("#vault-explorer-modal").count() == 1
    open_prompt(page, "Keep this prompt")
    page.once("dialog", lambda dialog: dialog.dismiss())
    page.locator('[data-vault-file-close="true"]').last.click()
    assert page.locator("#vault-selection-input").input_value() == "Keep this prompt"
    assert page.locator("#vault-explorer-modal").count() == 1
    page.locator("[data-quote-dismiss]").click()
    page.evaluate(
        "document.querySelector('#vault-file-modal-editor').value = 'Keep edits'"
    )
    page.once("dialog", lambda dialog: dialog.dismiss())
    page.locator('#vault-file-modal > [data-vault-file-close="true"]').click(
        position={"x": 20, "y": 400}
    )
    assert page.locator("#vault-file-modal-editor").input_value() == "Keep edits"
    assert page.locator("#vault-explorer-modal").count() == 1
    page.once("dialog", lambda dialog: dialog.accept())
    page.locator('[data-vault-file-back="true"]').click()
    assert page.locator("#vault-file-modal").count() == 0
    assert page.locator("#vault-explorer-modal").count() == 1
    assert page.evaluate("files.isPickerOpen()")


def test_immediate_send_keeps_reading_and_refreshes_without_losing_next_instruction(
    quote_page: Page,
) -> None:
    page = quote_page
    open_prompt(page)
    page.locator("[data-quote-send]").click()
    page.wait_for_function("typeof finishStream === 'function'")
    assert page.locator("#vault-file-modal").count() == 1
    page.evaluate("document.querySelector('#vault-file-modal-preview').scrollTop = 150")
    open_prompt(page, "Next instruction in progress")
    assert page.locator("[data-quote-send]").is_disabled()
    page.locator("#vault-selection-input").press("Control+Enter")
    assert page.evaluate("sent.length") == 1
    page.evaluate(
        "fileContents['Notes/A.md'] = 'Agent updated the reviewed document'; finishStream()"
    )
    expect(page.locator("#vault-file-modal-preview")).to_have_text(
        "Agent updated the reviewed document"
    )
    assert (
        page.locator("#vault-selection-input").input_value()
        == "Next instruction in progress"
    )
    expect(page.locator("[data-quote-send]")).to_be_enabled()
    assert page.locator("#chat-input").input_value() == "Existing draft"


def test_completion_refreshes_the_file_currently_being_read(quote_page: Page) -> None:
    page = quote_page
    open_prompt(page)
    page.locator("[data-quote-send]").click()
    page.wait_for_function("typeof finishStream === 'function'")
    page.evaluate("files.openFile('Notes/B.md')")
    expect(page.locator("#vault-file-modal-path")).to_have_text("Notes/B.md")
    page.evaluate("fileContents['Notes/B.md'] = 'Updated second file'; finishStream()")
    expect(page.locator("#vault-file-modal-preview")).to_have_text(
        "Updated second file"
    )


def test_dismissing_reader_during_send_does_not_reopen_it(quote_page: Page) -> None:
    page = quote_page
    open_prompt(page)
    page.locator("[data-quote-send]").click()
    page.wait_for_function("typeof finishStream === 'function'")
    page.locator('[data-vault-file-close="true"]').last.click()
    page.evaluate("finishStream()")
    page.wait_for_function("!state.isLoading")
    assert page.locator("#vault-file-modal").count() == 0


def test_failed_background_refresh_keeps_reader_and_reports_error(
    quote_page: Page,
) -> None:
    page = quote_page
    open_prompt(page)
    page.locator("[data-quote-send]").click()
    page.wait_for_function("typeof finishStream === 'function'")
    page.evaluate("failFileReads = true; finishStream()")
    expect(page.locator("#vault-file-modal-status")).to_contain_text(
        "Refresh unavailable"
    )
    assert page.locator("#vault-file-modal").count() == 1
    assert page.locator("#passage").count() == 1


def test_background_refresh_preserves_reading_scroll(quote_page: Page) -> None:
    page = quote_page
    open_prompt(page)
    page.locator("[data-quote-send]").click()
    page.wait_for_function("typeof finishStream === 'function'")
    preview = page.locator("#vault-file-modal-preview")
    preview.evaluate("element => element.scrollTop = 200")
    scroll = preview.evaluate("element => element.scrollTop")
    assert scroll > 0
    page.evaluate(
        "fileContents['Notes/A.md'] = 'Long update with a document edit'; finishStream()"
    )
    expect(preview).to_contain_text("Long update with a document edit")
    assert preview.evaluate("element => element.scrollTop") == scroll


def test_late_background_refresh_cannot_overwrite_local_edits(quote_page: Page) -> None:
    page = quote_page
    open_prompt(page)
    page.locator("[data-quote-send]").click()
    page.wait_for_function("typeof finishStream === 'function'")
    page.evaluate("""() => {
        const originalFetch = window.fetch;
        window.fetch = async (...args) => {
            const result = await originalFetch(...args);
            if (args[0].includes('/files?')) await new Promise(resolve => { window.releaseFileRead = resolve; });
            return result;
        };
        fileContents['Notes/A.md'] = 'Background edit';
        finishStream();
    }""")
    page.wait_for_function("typeof releaseFileRead === 'function' && !state.isLoading")
    page.locator('[data-vault-file-mode="edit"]').click()
    page.locator("#vault-file-modal-editor").fill("New local edits")
    page.evaluate("releaseFileRead()")
    page.wait_for_timeout(100)
    assert page.locator("#vault-file-modal-editor").input_value() == "New local edits"


def test_finishing_send_does_not_steal_focus_from_continued_reading(
    quote_page: Page,
) -> None:
    page = quote_page
    open_prompt(page)
    page.locator("[data-quote-send]").click()
    page.wait_for_function("typeof finishStream === 'function'")
    open_prompt(page, "Preparing the next instruction")
    preview = page.locator("#vault-file-modal-preview")
    preview.focus()
    page.evaluate("finishStream()")
    page.wait_for_function("!state.isLoading && fileReads.length > 1")
    expect(preview).to_be_focused()


def test_previous_refresh_cannot_unlock_the_next_pending_send(quote_page: Page) -> None:
    page = quote_page
    open_prompt(page)
    page.locator("[data-quote-send]").click()
    page.wait_for_function("typeof finishStream === 'function'")
    page.evaluate("""() => {
        const originalFetch = window.fetch;
        let held = false;
        window.fetch = async (...args) => {
            const result = await originalFetch(...args);
            if (!held && args[0].includes('/files?')) {
                held = true;
                await new Promise(resolve => { window.releaseFileRead = resolve; });
            }
            return result;
        };
        finishStream();
    }""")
    page.wait_for_function("!state.isLoading && typeof releaseFileRead === 'function'")
    open_prompt(page, "Next request")
    page.evaluate("holdStart = true")
    page.locator("[data-quote-send]").click()
    page.wait_for_function("typeof releaseStart === 'function'")
    assert page.locator("#vault-selection-input").get_attribute("readonly") is not None
    page.evaluate("releaseFileRead()")
    page.wait_for_timeout(100)
    assert page.locator("#vault-selection-input").get_attribute("readonly") is not None
    assert page.locator("[data-quote-dismiss]").is_disabled()
