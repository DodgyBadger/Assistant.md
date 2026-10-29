"""Failed tool effects and prior-run external commits constrain automatic replay."""

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.chat_failure_recovery_contracts import set_setting
from validation.scenarios.support.chat_persistence import PERSISTENCE_CONTEXT
from validation.scenarios.support.recovery_activity import require_recovery_activity
from validation.scenarios.support.recovery_effect_policy_workload import (
    effect_policy_server,
)
from validation.scenarios.support.rollback_restart_workload import (
    CHANGED,
    ORIGINAL,
    chat_with_redirects,
)

pytestmark = pytest.mark.scenario


@pytest.mark.parametrize(
    "mode", ["cross_run_vault", "failed_manual", "failed_unknown", "failed_readonly"]
)
async def test_real_effect_policies_preserve_external_commits_and_safe_resume(
    app: Application, mode
):
    vault = app.vault("RecoveryEffectPolicy")
    app.write(vault, "notes/transactional.md", ORIGINAL)
    app.write(vault, "AssistantMD/Authoring/full_history.md", PERSISTENCE_CONTEXT)
    await app.start()
    for name, value in (
        ("default_model", "test"),
        ("compaction_type", "none"),
        ("model_stream_retries", "2"),
        ("model_stream_retry_base_delay_seconds", "0"),
        ("model_stream_retry_max_delay_seconds", "0"),
        ("task_rollback_enabled", "true"),
    ):
        set_setting(app, name, value)
    target = vault / "notes/transactional.md"
    with effect_policy_server(app, target, mode) as server:
        result = await chat_with_redirects(
            app,
            {
                "vault_name": vault.name,
                "session_id": "effect-policy",
                "prompt": "Execute the configured bounded effect sequence.",
                "model": "code-tool-model",
                "context_template": "full_history.md",
            },
        )
        app.evidence.save_json(
            "effect-policy-outcome.json",
            {
                "mode": mode,
                "file": target.read_text(),
                "terminal": result["terminal"],
                "events": result["events"],
                "external_post_count": len(server.external_posts),
            },
        )
        assert len(result["task_ids"]) == 1
        assert not [
            row for row in result["events"] if row["event"] == "chat_retry_redirect"
        ]
        if mode == "failed_readonly":
            assert result["terminal"]["event"] == "done"
            assert server.effects == [ORIGINAL, ORIGINAL]
            assert not server.external_posts
            require_recovery_activity(
                app, "chat_retry_scheduled", strategy="resume_snapshot"
            )
        else:
            assert result["terminal"]["event"] == "error"
            assert len(server.external_posts) == 1
            reason = (
                "rollback_restart_external_or_unknown_effect"
                if mode == "cross_run_vault"
                else "failed_external_or_unknown_effect"
            )
            require_recovery_activity(
                app, "chat_recovery_rejected", strategy="manual_required", reason=reason
            )
            if mode == "cross_run_vault":
                assert server.disconnected
                require_recovery_activity(
                    app, "chat_retry_scheduled", strategy="resume_snapshot"
                )
                assert server.effects == [ORIGINAL + CHANGED]
                app.events.require(
                    "task_rollback_completed",
                    task_id=result["task_ids"][0],
                    paths_restored=1,
                )
        assert target.read_text() == ORIGINAL
