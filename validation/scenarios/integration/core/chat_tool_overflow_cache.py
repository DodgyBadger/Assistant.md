"""
Integration scenario that validates oversized chat tool output is stored in
cache and replaced with a compact cache-ref notice.
"""

import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from validation.core.base_scenario import BaseScenario


class ChatToolOverflowCacheScenario(BaseScenario):
    """Validate chat overflow handling uses cache instead of hidden buffering."""

    async def test_scenario(self):
        vault = self.create_vault("ChatToolOverflowCacheVault")

        await self.start_system()

        from pydantic_ai.models.test import TestModel

        import core.chat.executor as chat_executor
        from core.authoring.cache import get_cache_artifact
        from core.authoring.shared.tool_binding import resolve_tool_binding
        from core.runtime.state import get_runtime_context

        cache_ref_holder = {"value": ""}
        call_index = {"value": 0}

        async def overflow_probe() -> str:
            return "OVERFLOW_SEGMENT " * 1200

        class _CacheReadModel(TestModel):
            def __init__(self):
                super().__init__(call_tools=["code_execution"])

            def gen_tool_args(self, tool_def):
                if getattr(tool_def, "name", "") != "code_execution":
                    return super().gen_tool_args(tool_def)
                cache_ref = cache_ref_holder["value"]
                assert cache_ref, "Scenario must capture a cache ref before reading it"
                return {
                    "code": (
                        f"artifact = await read_cache(ref={cache_ref!r})\n"
                        'artifact.content if artifact.exists else "CACHE_NOT_FOUND"'
                    )
                }

        def _patched_prepare_agent_config(
            vault_name, vault_path, tools, model, thinking=None, chat_mode=None
        ):
            del vault_name, tools, model, thinking
            call_index["value"] += 1
            if call_index["value"] == 1:
                return (
                    "You must call the overflow_probe tool before responding.",
                    "",
                    TestModel(),
                    [overflow_probe],
                )
            if call_index["value"] == 2:
                binding = resolve_tool_binding(
                    ["code_execution"],
                    vault_path=vault_path,
                )
                return (
                    "You must call code_execution before responding.",
                    binding.tool_instructions,
                    _CacheReadModel(),
                    binding.tool_functions,
                )
            raise AssertionError(f"Unexpected chat call count: {call_index['value']}")

        original_prepare_agent_config = chat_executor._prepare_agent_config
        chat_executor._prepare_agent_config = _patched_prepare_agent_config
        try:
            update_setting = self.call_api(
                "/api/system/settings/general/auto_cache_max_tokens",
                method="PUT",
                data={"value": "50"},
            )
            assert (
                update_setting.status_code == 200
            ), "Scenario should lower chat overflow threshold"

            chat_result = await self.run_chat_task(
                {
                    "vault_name": vault.name,
                    "prompt": "Use the overflow_probe tool and then answer briefly.",
                    "session_id": "chat_tool_overflow_cache_session",
                    "tools": ["overflow_probe"],
                    "model": "test",
                },
            )
            assert (
                chat_result["start_response"].status_code == 200
            ), "Chat task with oversized tool result should start"
            assert (
                chat_result["terminal_event"].get("event") == "done"
            ), "Chat execution with oversized tool result should succeed"

            response_text = chat_result["text"]
            session_id = chat_result["session_id"]

            self.soft_assert(
                "stored in cache ref '" in response_text,
                "Response should mention cache ref storage",
            )
            self.soft_assert(
                "Preview:" in response_text, "Response should include a compact preview"
            )
            self.soft_assert(
                "artifact.content" in response_text,
                "Response should show how to access cached RetrievedItem content",
            )
            self.soft_assert(
                "buffer" not in response_text.lower(),
                "Response should not refer to buffer routing",
            )

            match = re.search(r"cache ref '([^']+)'", response_text)
            assert match, "Response should contain an extractable cache ref"
            cache_ref = match.group(1)
            cache_ref_holder["value"] = cache_ref
            self.soft_assert(
                cache_ref.startswith("tool/overflow_probe/"),
                "Cache ref should use the tool-scoped chat overflow namespace",
            )

            runtime = get_runtime_context()
            artifact = get_cache_artifact(
                owner_id=f"{vault.name}/chat/{session_id}",
                session_key=session_id,
                artifact_ref=cache_ref,
                now=datetime.now(),
                week_start_day=0,
                system_root=runtime.config.system_root,
            )
            assert (
                artifact is not None
            ), "Oversized chat tool output should be persisted in cache"

            self.soft_assert_equal(
                artifact["origin"],
                "chat_tool_overflow",
                "Cache artifact origin should be tracked",
            )
            self.soft_assert_equal(
                artifact["metadata"].get("tool_name"),
                "overflow_probe",
                "Cache artifact metadata should record the originating tool",
            )
            self.soft_assert(
                "OVERFLOW_SEGMENT" in artifact["raw_content"],
                "Cached artifact should preserve the oversized tool payload",
            )
            self.soft_assert(
                len(artifact["raw_content"]) > len(response_text),
                "Cached artifact should retain more content than the compact chat response",
            )

            persisted_events = chat_executor._CHAT_STORE.get_tool_events(
                session_id=session_id,
                vault_name=vault.name,
                limit=20,
            )
            overflow_events = [
                event
                for event in persisted_events
                if event.event_type == "overflow_cached"
            ]
            assert (
                overflow_events
            ), "Overflow cache event should be persisted for UI inspection"
            overflow_event = overflow_events[-1]
            self.soft_assert(
                "stored in cache ref '" in (overflow_event.result_text or ""),
                "Persisted overflow event should show the model-visible cache notice",
            )
            self.soft_assert(
                cache_ref in (overflow_event.result_text or ""),
                "Persisted overflow event should include the cache ref",
            )
            self.soft_assert(
                "code_execution" in (overflow_event.result_text or ""),
                "Persisted overflow event should include the cache read instruction",
            )
            self.soft_assert(
                "artifact.content" in (overflow_event.result_text or ""),
                "Persisted overflow event should preserve cached content guidance",
            )

            followup = await self.run_chat_task(
                {
                    "vault_name": vault.name,
                    "prompt": "Read the cached overflow artifact and return its content.",
                    "session_id": session_id,
                    "tools": ["code_execution"],
                    "model": "test",
                },
            )
            assert (
                followup["start_response"].status_code == 200
            ), "Cache read follow-up task should start"
            assert (
                followup["terminal_event"].get("event") == "done"
            ), "Cache read follow-up should succeed"
            self.soft_assert(
                "OVERFLOW_SEGMENT" in followup["text"],
                "code_execution should read the previously cached oversized chat tool output",
            )
            self.soft_assert(
                "CACHE_NOT_FOUND" not in followup["text"],
                "code_execution should use the same chat cache namespace as overflow storage",
            )
        finally:
            chat_executor._prepare_agent_config = original_prepare_agent_config
            await self.stop_system()
            self.teardown_scenario()
