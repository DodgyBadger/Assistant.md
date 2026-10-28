"""Real authoring boundaries with controlled history and configured test-model inputs."""

from __future__ import annotations

import pytest
from pydantic_ai.messages import ModelRequest, UserPromptPart

from validation.pytest_scenarios.application import Application
from validation.pytest_scenarios.support.authoring import (
    commit_compacted_history,
    default_context_vaults,
    process_context,
    read_markdown_with_media,
    system_prompt_text,
)

pytestmark = pytest.mark.scenario


async def test_default_context_sources_and_effective_history(app: Application) -> None:
    vault, vault_without_instructions = default_context_vaults(app)
    await app.start()
    image_read = read_markdown_with_media(vault, "Projects/WorkspaceA/readme.md")
    assert image_read.metadata["status"] == "completed"
    assert image_read.metadata["media_mode"] == "markdown+images"
    assert isinstance(
        image_read.return_value, list
    ), "Ordinary chat file reads should retain multimodal behavior"
    from core.authoring.context_manager import build_context_manager_history_processor

    session_id = "default_context_session"
    processor = build_context_manager_history_processor(
        session_id=session_id,
        vault_name=vault.name,
        vault_path=str(vault),
        model_alias="gpt",
        template_name="default.md",
        workspace_path="Projects/WorkspaceA",
    )
    processed = await process_context(
        app,
        processor,
        "What do you remember?",
        [
            ModelRequest(
                parts=[UserPromptPart(content="What do you remember?")],
                run_id="run-user-notes",
            )
        ],
    )
    system_text = system_prompt_text(processed)
    assert (
        "![File icon]" in system_text
    ), "Keep source Markdown text without embedding image data"
    assert not any(
        isinstance(getattr(part, "content", None), list)
        for message in processed
        for part in getattr(message, "parts", ())
    ), "Automatic standing context should not attach multimodal content"
    assert (
        "Use the validation soul instruction." in system_text
    ), "Expected default context to load AssistantMD/soul.md"
    assert (
        "Use the validation vault playbook." in system_text
    ), "Expected default context to load AssistantMD/playbook.md"
    assert (
        "SOUL_TAIL_SENTINEL" not in system_text
        and system_text.count("Soul filler that should eventually be truncated.") < 200
    ), "Expected explicit soul instructions to be bounded"
    assert (
        "VAULT_PLAYBOOK_TAIL_SENTINEL" not in system_text
        and system_text.count(
            "Vault playbook filler that should eventually be truncated."
        )
        < 200
    ), "Expected explicit vault playbook instructions to be bounded"
    assert (
        "This workspace is for validating workspace README loading." in system_text
    ), "Expected default context to load workspace README.md"
    assert (
        "Use the workspace-specific validation playbook." in system_text
    ), "Expected default context to load workspace playbook.md"
    assert (
        "## Custom Section" in system_text
    ), "Expected default context instructions to include user notes section"
    assert (
        "The user works on AssistantMD." in system_text
    ), "Expected user context note to be injected"
    assert (
        "Prefer concise engineering answers." in system_text
    ), "Expected preference context note to be injected"
    assert (
        "Custom context note sections are owned by the skill" in system_text
    ), "Expected default context to preserve custom context note sections"
    assert (
        "USER_NOTES_TAIL_SENTINEL" not in system_text
    ), "Expected oversized user notes file to be bounded"
    assert (
        system_text.count("Filler context note line") < 500
    ), "Expected default context to truncate oversized user notes content"
    from core.memory.session_map.checkpoints import build_session_map_context_message
    from core.utils.messages import extract_role_and_text

    compacted_session_id, compacted_raw_messages, effective_history, session_map = (
        commit_compacted_history(vault)
    )
    active_prompt = ModelRequest(
        parts=[UserPromptPart(content="Continue from the compacted context.")],
        run_id="run-active",
    )
    compacted_processor = build_context_manager_history_processor(
        session_id=compacted_session_id,
        vault_name=vault.name,
        vault_path=str(vault),
        model_alias="gpt",
        template_name="default.md",
        workspace_path="Projects/WorkspaceA",
    )
    compacted_processed = await process_context(
        app,
        compacted_processor,
        "Continue from the compacted context.",
        [*effective_history, active_prompt],
    )
    expected_map_message = build_session_map_context_message(session_map)
    assert extract_role_and_text(compacted_processed[1]) == extract_role_and_text(
        expected_map_message
    ), "Expected default context to preserve the effective session map after standing context"
    assert [type(message).__name__ for message in compacted_processed[2:-1]] == [
        type(message).__name__ for message in compacted_raw_messages[2:]
    ], "Expected default context to preserve provider-native retained-tail ordering"
    assert (
        getattr(compacted_processed[3].parts[0], "tool_call_id", None)
        == "call-compacted-tail"
    ), "Expected retained tool call identity to survive default context assembly"
    assert (
        getattr(compacted_processed[4].parts[0], "tool_call_id", None)
        == "call-compacted-tail"
    ), "Expected retained tool return identity to survive default context assembly"
    compacted_text = "\n".join(
        extract_role_and_text(message)[1] for message in compacted_processed
    )
    assert (
        "Use the validation soul instruction."
        in extract_role_and_text(compacted_processed[0])[1]
    ), "Expected fresh standing context ahead of the effective compacted history"
    assert (
        "CANONICAL_ONLY_EVICTED_SENTINEL" not in compacted_text
    ), "Expected default context not to reload canonical messages behind the checkpoint"
    assert (
        compacted_text.count("Continue from the compacted context.") == 1
    ), "Expected the active prompt exactly once after context assembly"
    assert extract_role_and_text(compacted_processed[-1]) == extract_role_and_text(
        active_prompt
    ), "Expected the active prompt to remain the final message"
    no_defaults_processor = build_context_manager_history_processor(
        session_id="default_context_no_defaults_session",
        vault_name=vault_without_instructions.name,
        vault_path=str(vault_without_instructions),
        model_alias="gpt",
        template_name="default.md",
    )
    no_defaults_processed = await process_context(
        app,
        no_defaults_processor,
        "Use defaults.",
        [
            ModelRequest(
                parts=[UserPromptPart(content="Use defaults.")],
                run_id="run-no-defaults",
            )
        ],
    )
    no_defaults_system_text = system_prompt_text(no_defaults_processed)
    assert all(
        source not in no_defaults_system_text
        for source in (
            "Use the validation soul instruction.",
            "Use the validation vault playbook.",
            "The user works on AssistantMD.",
        )
    ), "Empty vault must not inherit another vault's controlled instruction sources"
    missing_readme_processor = build_context_manager_history_processor(
        session_id="default_context_missing_readme_session",
        vault_name=vault.name,
        vault_path=str(vault),
        model_alias="gpt",
        template_name="default.md",
        workspace_path="Projects/WorkspaceB",
    )
    missing_readme_processed = await process_context(
        app,
        missing_readme_processor,
        "Scan the workspace.",
        [
            ModelRequest(
                parts=[UserPromptPart(content="Scan the workspace.")],
                run_id="run-missing-readme",
            )
        ],
    )
    missing_readme_system_text = system_prompt_text(missing_readme_processed)
    assert (
        "Projects/WorkspaceB" in missing_readme_system_text
    ), "Expected workspace instructions to include the workspace path without a README"
    assert (
        "This workspace is for validating workspace README loading."
        not in missing_readme_system_text
    ), "Missing README must not reuse the other workspace's README content"
    activity_log = app.request("/api/system/activity-log")
    assert activity_log.status_code == 200, "Activity log fetch should succeed"
    activity_rows = [entry.get("data", {}) for entry in activity_log.json()["entries"]]
    assert any(
        row.get("event") == "context_template_run_completed"
        and row.get("template_name") == "default.md"
        and (row.get("workspace_path") == "Projects/WorkspaceA")
        and (row.get("summary_section_count") == 1)
        for row in activity_rows
    )
