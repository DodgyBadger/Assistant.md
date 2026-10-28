"""Legacy checkpoint retained messages use unique canonical origins."""

import pytest

from validation.pytest_scenarios.application import Application
from validation.pytest_scenarios.support.fork_lineage_inputs import (
    _assistant,
    _map_context,
    _user,
)

pytestmark = pytest.mark.scenario


async def test_legacy_checkpoint_uniquely_retained_origin(app: Application):
    vault = app.vault("LegacyForkOrigins")
    await app.start()
    from core.identity import LOCAL_USER_PRINCIPAL_ID
    from core.runtime.state import get_runtime_context

    store = get_runtime_context().chat_store
    session_id = "legacy-checkpoint-origin-projection"
    canonical = [
        _user("First question"),
        _assistant("First answer"),
        _user("Retained question"),
        _assistant("Retained answer"),
    ]
    store.ensure_session(
        session_id, vault.name, owner_principal_id=LOCAL_USER_PRINCIPAL_ID
    )
    store.add_messages(session_id, vault.name, canonical)
    context = _map_context("Legacy recovery context")
    store.add_context_checkpoint(
        session_id=session_id,
        vault_name=vault.name,
        checkpoint_id="legacy-origin-checkpoint",
        checkpoint_kind="recovery_card",
        source="validation",
        message_count_before=4,
        last_message_sequence_index=3,
        summary_message=context,
        replacement_history=[context, canonical[-1]],
        replacement_source_sequence_indexes=None,
    )
    effective = store.get_stored_messages(session_id, vault.name)
    assert [message.fork_sequence_index for message in effective] == [None, 3]
