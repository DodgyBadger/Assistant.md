"""The packaged notes workflow skips short notes without invoking curation."""

from datetime import datetime

import pytest

from validation.scenarios.application import Application

pytestmark = pytest.mark.scenario


async def test_small_user_notes_skip_packaged_compaction(app: Application) -> None:
    vault = app.vault("UserNotesCompactionWorkflowVault")
    notes = "# User Notes\n\n## User\n\n- The user works on AssistantMD.\n"
    app.write(vault, "AssistantMD/user.md", notes)
    await app.start()
    from core.authoring.runtime import WorkflowAuthoringHost, run_authoring_monty
    from core.authoring.template_loader import load_authoring_template_file
    from core.identity import LOCAL_USER_AUTHORITY, use_execution_authority

    source = load_authoring_template_file(
        "core/authoring/seed_templates/workflows/nightly-user-notes-compaction.md"
    )
    workflow_id = f"{vault.name}/system/nightly-user-notes-compaction"
    with use_execution_authority(LOCAL_USER_AUTHORITY):
        result = await run_authoring_monty(
            workflow_id=workflow_id,
            code=source.code,
            host=WorkflowAuthoringHost(
                workflow_id=workflow_id,
                vault_path=str(vault),
                reference_date=datetime(2026, 5, 23, 2, 30, 0),
            ),
            script_name="nightly-user-notes-compaction.md",
        )
    assert result.status == "skipped"
    assert "trigger is" in result.reason
    assert (vault / "AssistantMD/user.md").read_text(encoding="utf-8") == notes
