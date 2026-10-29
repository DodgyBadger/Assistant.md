"""Native persisted conversation/map inputs and real UI session navigation."""

from playwright.async_api import expect

from validation.scenarios.support.session_map_journey_inputs import (
    conversation,
    seed_session,
)


def thread(application, session, pairs, title=None):
    from core.runtime.state import get_runtime_context

    store = get_runtime_context().chat_store
    seed_session(store, session, application.vault.name, conversation(pairs))
    store.set_session_title(session, application.vault.name, title or session)
    return store


async def session_list(ui, application):
    if ui.url.rstrip("/") != application.url:
        await ui.goto(application.url)
    selector = ui.locator("#vault-selector")
    if await selector.input_value() != application.vault.name:
        await selector.select_option(application.vault.name)
    await ui.get_by_role("button", name="Open chat settings", exact=True).click()
    dialog = ui.get_by_role("dialog", name="Chat Settings", exact=True)
    await expect(dialog).to_be_visible()
    return dialog


async def load_thread(ui, application, session):
    dialog = await session_list(ui, application)
    row = dialog.locator(f'[data-session-browser-row-id="{session}"]')
    await expect(row).to_be_visible()
    await row.click()
    await expect(dialog).to_have_count(0)


def map_checkpoint(store, session, vault, checkpoint_id, through):
    """Consistent native checkpoint input; does not claim to test map authorship."""
    from core.memory.session_map.checkpoints import build_session_map_context_message
    from core.memory.session_map.models import (
        SessionMapDraft,
        SessionMapTrajectory,
        SourceRange,
    )

    draft = SessionMapDraft(
        trajectory=SessionMapTrajectory(
            text="Earlier conversation retained in the canonical timeline.",
            sources=(SourceRange(start=0, end=through),),
        )
    )
    summary = build_session_map_context_message(draft)
    store.add_context_checkpoint(
        session_id=session,
        vault_name=vault,
        checkpoint_id=checkpoint_id,
        checkpoint_kind="session_map",
        source="validation",
        message_count_before=len(store.get_history(session, vault, mode="raw")),
        last_message_sequence_index=through,
        summary_message=summary,
        replacement_history=[summary],
        metadata={
            "map": draft.model_dump(mode="json"),
            "map_observed_through_sequence_index": through,
        },
    )


async def session_options(ui, application):
    dialog = await session_list(ui, application)
    await dialog.locator("summary").filter(has_text="Options").click()
    return dialog
