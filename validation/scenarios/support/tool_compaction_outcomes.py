"""Real chat tool compaction input setup and result observations."""

import json

from validation.scenarios.support.compaction_journeys import add_turn


def seed_compactable(store, vault, session):
    store.ensure_session(session, vault, owner_principal_id="local-user")
    add_turn(store, vault, session, "Older request.", "Older response.")
    add_turn(store, vault, session, "Recent request.", "Recent response.")


def compaction_results(store, vault, session):
    rows = [
        row
        for row in store.get_tool_events(session, vault)
        if row.tool_name == "chat_history_compact" and row.event_type == "result"
    ]
    return [json.loads(row.result_text) for row in rows]
