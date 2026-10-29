"""A failed transactional append must not be replayed over its partial mutation."""

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.chat_failure_recovery_contracts import set_setting
from validation.scenarios.support.chat_persistence import PERSISTENCE_CONTEXT
from validation.scenarios.support.rollback_restart_workload import (
    CHANGED,
    ORIGINAL,
    chat_with_redirects,
)
from validation.scenarios.support.settled_read_failed_append import (
    settled_read_append_server,
)

pytestmark = pytest.mark.scenario


async def test_completed_read_does_not_hide_failed_transactional_append(
    app: Application,
):
    vault = app.vault("FailedAppendSafety")
    app.write(vault, "notes/transactional.md", ORIGINAL)
    app.write(vault, "AssistantMD/Authoring/full_history.md", PERSISTENCE_CONTEXT)
    await app.start()
    for name, value in (
        ("default_model", "test"),
        ("compaction_type", "none"),
        ("model_stream_retries", "1"),
        ("model_stream_retry_base_delay_seconds", "0"),
        ("model_stream_retry_max_delay_seconds", "0"),
        ("task_rollback_enabled", "true"),
    ):
        set_setting(app, name, value)
    target = vault / "notes/transactional.md"
    with settled_read_append_server(app, target) as server:
        result = await chat_with_redirects(
            app,
            {
                "vault_name": vault.name,
                "session_id": "settled-read-failed-append",
                "prompt": "Read the file, then execute the transactional append.",
                "model": "code-tool-model",
                "context_template": "full_history.md",
            },
        )
        app.evidence.save_json(
            "settled-read-append-safety-observation.json",
            {
                "final_file": target.read_text(),
                "expected_single_append": ORIGINAL + CHANGED,
                "service_observations": server.effects,
                "events": result["events"],
            },
        )
        assert result["terminal"]["event"] == "done"
        assert (
            target.read_text() == ORIGINAL + CHANGED
        ), "Automatic recovery must roll back an escaped failed append before replay"
        assert (
            len(
                [
                    row
                    for row in result["events"]
                    if row["event"] == "chat_retry_redirect"
                ]
            )
            == 1
        )
        assert server.effects == [ORIGINAL + CHANGED, ORIGINAL + CHANGED]
