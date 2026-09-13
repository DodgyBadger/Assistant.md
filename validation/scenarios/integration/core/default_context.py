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

from pydantic_ai.messages import ModelRequest, UserPromptPart

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
