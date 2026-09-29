"""
Integration scenario for packaged default context loading behavior.

Validates that the default context template loads only explicit vault-level
instructions and bounds user-controlled context sources without depending on
their internal Markdown structure.
"""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from validation.core.base_scenario import BaseScenario, with_local_user_authority


class DefaultContextScenario(BaseScenario):
    """Ensure default.md composes vault, workspace, and user context."""

    @with_local_user_authority
    async def test_scenario(self):
        vault = self.create_vault("DefaultContextVault")
        self.create_file(
            vault,
            "AssistantMD/soul.md",
            """# Soul

Use the validation soul instruction.
"""
            + ("Soul filler that should eventually be truncated.\n" * 200),
        )
        self.create_file(
            vault,
            "AssistantMD/PlayBook.md",
            """# Vault Playbook

Use the validation vault playbook.
"""
            + ("Vault playbook filler that should eventually be truncated.\n" * 200),
        )
        self.create_file(
            vault,
            "AssistantMD/user.md",
            """# User Notes

## User

- The user works on AssistantMD.

## Chat And Work Preferences

- Prefer concise engineering answers.

## Custom Section

- Custom context note sections are owned by the skill, not the context script.

## Large Section

"""
            + (
                "- Filler context note line that should eventually be truncated.\n"
                * 500
            )
            + """
            """,
        )
        vault_without_instructions = self.create_vault("DefaultContextEmptyVault")
        self.create_file(
            vault,
            "Projects/WorkspaceA/readme.md",
            """# Workspace A

This workspace is for validating workspace README loading.
""",
        )
        self.create_file(
            vault,
            "Projects/WorkspaceA/PLAYBOOK.md",
            """# Workspace Playbook

Use the workspace-specific validation playbook.
""",
        )
        self.create_file(
            vault,
            "Projects/WorkspaceB/notes.md",
            """# Workspace B Notes

This workspace intentionally has no README.
""",
        )

        await self.start_system()

        from core.authoring.context_manager import (
            build_context_manager_history_processor,
        )

        session_id = "default_context_session"
        processor = build_context_manager_history_processor(
            session_id=session_id,
            vault_name=vault.name,
            vault_path=str(vault),
            model_alias="gpt",
            template_name="default.md",
            workspace_path="Projects/WorkspaceA",
        )

        processed = await processor(
            SimpleNamespace(prompt="What do you remember?", deps=SimpleNamespace()),
            [
                ModelRequest(
                    parts=[UserPromptPart(content="What do you remember?")],
                    run_id="run-user-notes",
                )
            ],
        )

        system_text = "\n\n".join(
            getattr(part, "content", "")
            for message in processed
            for part in getattr(message, "parts", ())
            if getattr(part, "part_kind", None) == "system-prompt"
        )

        self.soft_assert(
            "Use the validation soul instruction." in system_text,
            "Expected default context to load AssistantMD/soul.md",
        )
        self.soft_assert(
            "Use the validation vault playbook." in system_text,
            "Expected default context to load AssistantMD/playbook.md",
        )
        self.soft_assert(
            "[Soul truncated by default context script.]" in system_text,
            "Expected explicit soul instructions to be bounded",
        )
        self.soft_assert(
            "[Vault playbook truncated by default context script.]" in system_text,
            "Expected explicit vault playbook instructions to be bounded",
        )
        self.soft_assert(
            "This workspace is for validating workspace README loading." in system_text,
            "Expected default context to load workspace README.md",
        )
        self.soft_assert(
            "Use the workspace-specific validation playbook." in system_text,
            "Expected default context to load workspace playbook.md",
        )
        self.soft_assert(
            "## User Notes" in system_text,
            "Expected default context instructions to include user notes section",
        )
        self.soft_assert(
            "The user works on AssistantMD." in system_text,
            "Expected user context note to be injected",
        )
        self.soft_assert(
            "Prefer concise engineering answers." in system_text,
            "Expected preference context note to be injected",
        )
        self.soft_assert(
            "Custom context note sections are owned by the skill" in system_text,
            "Expected default context to preserve custom context note sections",
        )
        self.soft_assert(
            "[User notes truncated by default context script.]" in system_text,
            "Expected oversized user notes file to be bounded",
        )
        self.soft_assert(
            system_text.count("Filler context note line") < 500,
            "Expected default context to truncate oversized user notes content",
        )

        from core.chat.compaction import CanonicalEvictionEnvelope
        from core.identity import LOCAL_USER_PRINCIPAL_ID
        from core.memory.session_map.checkpoints import (
            build_session_map_context_message,
            commit_session_map_checkpoint,
        )
        from core.memory.session_map.models import (
            SessionMapDraft,
            SessionMapEntry,
            SessionMapTrajectory,
            SourceRange,
        )
        from core.runtime.state import get_runtime_context
        from core.utils.messages import extract_role_and_text

        compacted_session_id = "default_context_compacted_session"
        store = get_runtime_context().chat_store
        store.ensure_session(
            compacted_session_id,
            vault.name,
            owner_principal_id=LOCAL_USER_PRINCIPAL_ID,
        )
        compacted_raw_messages = [
            ModelRequest(
                parts=[UserPromptPart(content="CANONICAL_ONLY_EVICTED_SENTINEL")],
                run_id="run-compacted",
            ),
            ModelResponse(
                parts=[TextPart(content="I will retain the active objective.")],
                run_id="run-compacted",
            ),
            ModelRequest(
                parts=[UserPromptPart(content="Inspect the current artifact.")],
                run_id="run-tail",
            ),
            ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="file_read",
                        args={"operation": "read", "path": "artifact.md"},
                        tool_call_id="call-compacted-tail",
                    )
                ],
                run_id="run-tail",
            ),
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        tool_name="file_read",
                        content="Current artifact evidence.",
                        tool_call_id="call-compacted-tail",
                    )
                ],
                run_id="run-tail",
            ),
            ModelResponse(
                parts=[TextPart(content="The artifact is current.")],
                run_id="run-tail",
            ),
        ]
        store.add_messages(
            compacted_session_id,
            vault.name,
            compacted_raw_messages,
        )
        compacted_revision = store.get_session_history_revision(
            compacted_session_id,
            vault.name,
        )
        session_map = SessionMapDraft(
            trajectory=SessionMapTrajectory(
                text="The session established an artifact-review objective.",
                sources=(SourceRange(start=0, end=1),),
            ),
            entries=(
                SessionMapEntry(
                    id="artifact_review",
                    kind="goal",
                    state="active",
                    basis="user_established",
                    text="Review the current artifact.",
                    sources=(SourceRange(start=0, end=1),),
                ),
            ),
        )
        commit_session_map_checkpoint(
            store=store,
            session_id=compacted_session_id,
            vault_name=vault.name,
            draft=session_map,
            previous_map=SessionMapDraft(),
            envelopes=(
                CanonicalEvictionEnvelope(
                    envelope_id="default-context-compacted-envelope",
                    session_id=compacted_session_id,
                    vault_name=vault.name,
                    history_revision=compacted_revision,
                    source_start_sequence_index=0,
                    source_end_sequence_index=1,
                    message_count=2,
                    estimated_tokens=20,
                    projected_text="Compacted objective evidence.",
                    source_digest="a" * 64,
                ),
            ),
            expected_history_revision=compacted_revision,
            message_count_before=len(compacted_raw_messages),
            source="validation",
            checkpoint_id="default-context-compacted-checkpoint",
        )
        effective_history = store.get_history(compacted_session_id, vault.name) or []
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
        compacted_processed = await compacted_processor(
            SimpleNamespace(
                prompt="Continue from the compacted context.",
                deps=SimpleNamespace(),
            ),
            [*effective_history, active_prompt],
        )
        expected_map_message = build_session_map_context_message(session_map)
        self.soft_assert_equal(
            extract_role_and_text(compacted_processed[1]),
            extract_role_and_text(expected_map_message),
            "Expected default context to preserve the effective session map after standing context",
        )
        self.soft_assert_equal(
            [type(message).__name__ for message in compacted_processed[2:-1]],
            [type(message).__name__ for message in compacted_raw_messages[2:]],
            "Expected default context to preserve provider-native retained-tail ordering",
        )
        self.soft_assert_equal(
            getattr(compacted_processed[3].parts[0], "tool_call_id", None),
            "call-compacted-tail",
            "Expected retained tool call identity to survive default context assembly",
        )
        self.soft_assert_equal(
            getattr(compacted_processed[4].parts[0], "tool_call_id", None),
            "call-compacted-tail",
            "Expected retained tool return identity to survive default context assembly",
        )
        compacted_text = "\n".join(
            extract_role_and_text(message)[1] for message in compacted_processed
        )
        self.soft_assert(
            "Use the validation soul instruction."
            in extract_role_and_text(compacted_processed[0])[1],
            "Expected fresh standing context ahead of the effective compacted history",
        )
        self.soft_assert(
            "CANONICAL_ONLY_EVICTED_SENTINEL" not in compacted_text,
            "Expected default context not to reload canonical messages behind the checkpoint",
        )
        self.soft_assert_equal(
            compacted_text.count("Continue from the compacted context."),
            1,
            "Expected the active prompt exactly once after context assembly",
        )
        self.soft_assert_equal(
            extract_role_and_text(compacted_processed[-1]),
            extract_role_and_text(active_prompt),
            "Expected the active prompt to remain the final message",
        )

        no_defaults_processor = build_context_manager_history_processor(
            session_id="default_context_no_defaults_session",
            vault_name=vault_without_instructions.name,
            vault_path=str(vault_without_instructions),
            model_alias="gpt",
            template_name="default.md",
        )
        no_defaults_processed = await no_defaults_processor(
            SimpleNamespace(prompt="Use defaults.", deps=SimpleNamespace()),
            [
                ModelRequest(
                    parts=[UserPromptPart(content="Use defaults.")],
                    run_id="run-no-defaults",
                )
            ],
        )
        no_defaults_system_text = "\n\n".join(
            getattr(part, "content", "")
            for message in no_defaults_processed
            for part in getattr(message, "parts", ())
            if getattr(part, "part_kind", None) == "system-prompt"
        )
        self.soft_assert(
            "Default stance: concise and curious." not in no_defaults_system_text
            and "## Vault Work Policy" not in no_defaults_system_text
            and "## Goal Tracking" not in no_defaults_system_text,
            "Expected missing soul and playbook files to inject no hidden defaults",
        )

        missing_readme_processor = build_context_manager_history_processor(
            session_id="default_context_missing_readme_session",
            vault_name=vault.name,
            vault_path=str(vault),
            model_alias="gpt",
            template_name="default.md",
            workspace_path="Projects/WorkspaceB",
        )

        missing_readme_processed = await missing_readme_processor(
            SimpleNamespace(prompt="Scan the workspace.", deps=SimpleNamespace()),
            [
                ModelRequest(
                    parts=[UserPromptPart(content="Scan the workspace.")],
                    run_id="run-missing-readme",
                )
            ],
        )

        missing_readme_system_text = "\n\n".join(
            getattr(part, "content", "")
            for message in missing_readme_processed
            for part in getattr(message, "parts", ())
            if getattr(part, "part_kind", None) == "system-prompt"
        )
        self.soft_assert(
            "The current chat workspace is `Projects/WorkspaceB`."
            in missing_readme_system_text,
            "Expected workspace instructions to include the workspace path without a README",
        )
        self.soft_assert(
            "No workspace README was found." in missing_readme_system_text,
            "Expected default context to explain missing workspace README handling",
        )
        self.soft_assert(
            "scan the workspace" in missing_readme_system_text
            and "create a README.md" in missing_readme_system_text,
            "Expected default context to suggest creating a workspace README",
        )

        activity_log = self.call_api("/api/system/activity-log")
        self.soft_assert_equal(
            activity_log.status_code, 200, "Activity log fetch should succeed"
        )
        activity_content = json.dumps(activity_log.json()["entries"])
        self.soft_assert(
            '"event": "context_template_run_completed"' in activity_content,
            "Expected activity log to include context-template completion event",
        )
        self.soft_assert(
            '"template_name": "default.md"' in activity_content,
            "Expected context-template activity to include template name",
        )
        self.soft_assert(
            '"workspace_path": "Projects/WorkspaceA"' in activity_content,
            "Expected context-template activity to include workspace path",
        )
        self.soft_assert(
            '"summary_section_count": 1' in activity_content,
            "Expected context-template activity to include summary section count",
        )

        await self.stop_system()
        self.teardown_scenario()
        self.assert_no_failures()
