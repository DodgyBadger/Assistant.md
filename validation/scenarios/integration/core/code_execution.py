"""
Integration scenario for the chat-facing code_execution tool.

Validates deterministic chat-scoped execution through the task-owned chat API
using a patched TestModel argument generator.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from validation.core.base_scenario import BaseScenario


class CodeExecutionScenario(BaseScenario):
    """Validate code_execution helper parity and simplified runtime access."""

    async def test_scenario(self):
        vault = self.create_vault("CodeExecutionVault")
        self.create_file(vault, "notes/structured.md", STRUCTURED_NOTE)
        self.create_file(vault, "notes/blocked.md", "BLOCKED_CONTENT")
        self.create_file(vault, "tasks/pending-one.md", "PENDING_ONE")
        self.copy_files("validation/templates/files/test_image.jpg", vault, "images")

        await self.start_system()

        from pydantic_ai.models.test import TestModel

        import core.chat.executor as chat_executor
        from core.authoring.cache import upsert_cache_artifact

        current_case = {"name": "allow_memory_read"}

        class _DeterministicToolModel(TestModel):
            def __init__(self):
                super().__init__(call_tools=["code_execution"])

            def gen_tool_args(self, tool_def):
                if getattr(tool_def, "name", "") != "code_execution":
                    return super().gen_tool_args(tool_def)

                case_name = current_case["name"]
                if case_name == "allow_memory_read":
                    return {
                        "code": (
                            'history = await retrieve_history(scope="session", limit=1)\n'
                            "str(history.item_count)"
                        )
                    }
                if case_name == "discovery":
                    return {}
                if case_name == "allow_cache_read":
                    return {
                        "code": (
                            'artifact = await read_cache(ref="tool/web_extract/call_seeded")\n'
                            'artifact.content if artifact.exists else "CACHE_NOT_FOUND"'
                        )
                    }
                if case_name == "allow_file_read":
                    return {
                        "code": (
                            'doc = await file_read(operation="read", path="notes/blocked.md")\n'
                            "doc.return_value"
                        )
                    }
                if case_name == "dynamic_attribute_helpers":
                    return {
                        "code": (
                            'doc = await file_read(operation="read", path="notes/blocked.md")\n'
                            'hasattr(doc, "return_value") and getattr(doc, "return_value") == "BLOCKED_CONTENT"'
                        )
                    }
                if case_name == "streaming_detail":
                    return {
                        "code": (
                            'padding = "' + ("a" * 260) + '"\n'
                            '"STREAM_RESULT_' + ("b" * 300) + '"'
                        )
                    }
                if case_name == "allow_write":
                    return {
                        "code": (
                            'await file_write(operation="write", path="notes/derived.md", content="DERIVED_RESULT")\n'
                            '"WRITE_OK"'
                        )
                    }
                if case_name == "allow_image_input":
                    return {
                        "code": (
                            "draft = await delegate(\n"
                            '    prompt="Read images/test_image.jpg and reply with IMAGE_OK.",\n'
                            '    tools=["file_read"],\n'
                            '    model="test",\n'
                            ")\n"
                            "draft.return_value"
                        )
                    }
                if case_name == "full_surface":
                    return {
                        "code": (
                            'cached = await read_cache(ref="tool/web_extract/call_seeded_full_surface")\n'
                            'await file_read(operation="read", path="notes/structured.md")\n'
                            f"parsed = await parse_markdown(value={STRUCTURED_NOTE!r})\n"
                            'listed = await file_read(operation="list", path="tasks")\n'
                            "pending = await pending_files(\n"
                            '    operation="get",\n'
                            "    items=listed,\n"
                            ")\n"
                            'await pending_files(operation="complete", items=(pending.items[0],))\n'
                            'history = await retrieve_history(scope="session", limit=1)\n'
                            "assembled = await assemble_context(\n"
                            "    history=history.items,\n"
                            '    instructions="Keep the output concise.",\n'
                            ")\n"
                            "draft = await delegate(\n"
                            "    prompt=(\n"
                            '        f"heading={parsed.sections[1].heading}; "\n'
                            '        f"cached={cached.content}; "\n'
                            '        f"messages={len(assembled.messages)}; "\n'
                            "        f\"listed={listed.metadata.get('file_count')}\"\n"
                            "    ),\n"
                            '    instructions="Return one short deterministic line.",\n'
                            '    model="test",\n'
                            ")\n"
                            'await file_write(operation="write", path="notes/full-surface.md", content=draft.return_value)\n'
                            'await finish(status="completed", reason="full-surface-ok")\n'
                            '"UNREACHABLE"'
                        )
                    }
                raise AssertionError(f"Unexpected scenario case: {case_name}")

        def _patched_prepare_agent_config(
            vault_name, vault_path, tools, model, thinking=None, chat_mode=None
        ):
            del vault_name, vault_path, tools, model, thinking
            from core.authoring.shared.tool_binding import resolve_tool_binding

            binding = resolve_tool_binding(
                ["code_execution"],
                vault_path=str(vault),
            )
            return (
                "You must call code_execution before responding.",
                binding.tool_instructions,
                _DeterministicToolModel(),
                binding.tool_functions,
            )

        original_prepare_agent_config = chat_executor._prepare_agent_config
        chat_executor._prepare_agent_config = _patched_prepare_agent_config
        try:
            upsert_cache_artifact(
                owner_id=f"{vault.name}/chat/code_execution_allow_cache_read",
                session_key="code_execution_allow_cache_read",
                artifact_ref="tool/web_extract/call_seeded",
                cache_mode="session",
                ttl_seconds=None,
                raw_content="SEEDED_CACHE_CONTENT",
                metadata={"origin": "validation"},
                origin="validation",
                now=datetime(2026, 4, 7, 12, 0, 0),
                week_start_day=0,
            )
            upsert_cache_artifact(
                owner_id=f"{vault.name}/chat/code_execution_full_surface",
                session_key="code_execution_full_surface",
                artifact_ref="tool/web_extract/call_seeded_full_surface",
                cache_mode="session",
                ttl_seconds=None,
                raw_content="FULL_SURFACE_CACHE_CONTENT",
                metadata={"origin": "validation"},
                origin="validation",
                now=datetime(2026, 4, 7, 12, 0, 0),
                week_start_day=0,
            )

            allow_read = await self.run_chat_task(
                {
                    "vault_name": vault.name,
                    "prompt": "Read session history through code_execution.",
                    "session_id": "code_execution_allow_read",
                    "tools": ["code_execution"],
                    "model": "test",
                },
            )
            assert (
                allow_read["start_response"].status_code == 200
            ), "Memory read task should start"
            assert (
                allow_read["terminal_event"].get("event") == "done"
            ), "Memory read should succeed"
            allow_text = allow_read["text"]
            self.soft_assert(
                "failed:" not in allow_text.lower(),
                "Memory read should not return a Monty failure",
            )
            self.soft_assert(
                any(digit in allow_text for digit in ("0", "1")),
                "Memory read should return a structured item count",
            )

            current_case["name"] = "allow_cache_read"
            allow_cache_read = await self.run_chat_task(
                {
                    "vault_name": vault.name,
                    "prompt": "Read a cached oversized tool artifact through code_execution.",
                    "session_id": "code_execution_allow_cache_read",
                    "tools": ["code_execution"],
                    "model": "test",
                },
            )
            assert (
                allow_cache_read["start_response"].status_code == 200
            ), "Cache read task should start"
            assert (
                allow_cache_read["terminal_event"].get("event") == "done"
            ), "Cache read should succeed"
            allow_cache_read_text = allow_cache_read["text"]
            self.soft_assert(
                "SEEDED_CACHE_CONTENT" in allow_cache_read_text,
                "Cache read should return the cached artifact content",
            )

            current_case["name"] = "discovery"
            discovery = await self.run_chat_task(
                {
                    "vault_name": vault.name,
                    "prompt": "Inspect code_execution with no arguments first.",
                    "session_id": "code_execution_discovery",
                    "tools": ["code_execution"],
                    "model": "test",
                },
            )
            assert (
                discovery["start_response"].status_code == 200
            ), "No-arg code_execution task should start"
            assert (
                discovery["terminal_event"].get("event") == "done"
            ), "No-arg code_execution discovery should succeed"
            discovery_text = discovery["text"]
            self.soft_assert(
                bool(discovery_text.strip()),
                "No-arg code_execution should return a non-empty discovery response",
            )

            current_case["name"] = "allow_file_read"
            allow_file_read = await self.run_chat_task(
                {
                    "vault_name": vault.name,
                    "prompt": "Read a vault file through code_execution.",
                    "session_id": "code_execution_allow_file_read",
                    "tools": ["code_execution", "file_read"],
                    "model": "test",
                },
            )
            assert (
                allow_file_read["start_response"].status_code == 200
            ), "Vault file read task should start"
            assert (
                allow_file_read["terminal_event"].get("event") == "done"
            ), "Vault file read should succeed"
            allow_file_read_text = allow_file_read["text"]
            self.soft_assert(
                "BLOCKED_CONTENT" in allow_file_read_text,
                "Vault file read should return the file content",
            )

            current_case["name"] = "dynamic_attribute_helpers"
            dynamic_attribute_helpers = await self.run_chat_task(
                {
                    "vault_name": vault.name,
                    "prompt": "Exercise dynamic Monty attribute helpers.",
                    "session_id": "code_execution_dynamic_attribute_helpers",
                    "tools": ["code_execution", "file_read"],
                    "model": "test",
                },
            )
            assert (
                dynamic_attribute_helpers["start_response"].status_code == 200
            ), "Dynamic attribute helper task should start"
            assert (
                dynamic_attribute_helpers["terminal_event"].get("event") == "done"
            ), "Dynamic attribute helper run should complete"
            dynamic_attribute_helpers_text = dynamic_attribute_helpers["text"]
            self.soft_assert(
                "true" in dynamic_attribute_helpers_text.lower(),
                "code_execution should support hasattr/getattr on helper result objects",
            )

            current_case["name"] = "streaming_detail"
            streaming_detail = await self.run_chat_task(
                {
                    "vault_name": vault.name,
                    "prompt": "Stream code_execution detail payloads.",
                    "session_id": "code_execution_streaming_detail",
                    "tools": ["code_execution", "file_read"],
                    "model": "test",
                },
            )
            assert (
                streaming_detail["start_response"].status_code == 200
            ), "Streaming detail task should start"
            assert (
                streaming_detail["terminal_event"].get("event") == "done"
            ), "Streaming detail run should succeed"
            streaming_events = streaming_detail["events"]
            self.soft_assert(
                any(
                    event.get("event") == "tool_call_started"
                    and event.get("tool_name") == "code_execution"
                    and "arguments" not in event
                    and "arguments_detail" not in event
                    for event in streaming_events
                ),
                "Streaming tool start events should omit arguments",
            )
            self.soft_assert(
                any(
                    event.get("event") == "tool_call_finished"
                    and event.get("tool_name") == "code_execution"
                    and isinstance(event.get("token_count"), int)
                    and event["token_count"] >= 0
                    and "result" not in event
                    and "result_detail" not in event
                    and "result_metadata" not in event
                    and "artifact_ref" not in event
                    for event in streaming_events
                ),
                "Streaming tool finish events should expose only safe result metadata",
            )

            current_case["name"] = "allow_write"
            allow_write = await self.run_chat_task(
                {
                    "vault_name": vault.name,
                    "prompt": "Write a derived cache artifact through code_execution.",
                    "session_id": "code_execution_allow_write",
                    "tools": ["code_execution", "file_write"],
                    "model": "test",
                },
            )
            assert (
                allow_write["start_response"].status_code == 200
            ), "Allowed cache write task should start"
            assert (
                allow_write["terminal_event"].get("event") == "done"
            ), "Allowed cache write should succeed"
            write_text = allow_write["text"]
            self.soft_assert(
                "WRITE_OK" in write_text,
                "Allowed cache write should return the snippet result",
            )

            current_case["name"] = "allow_image_input"
            checkpoint = self.event_checkpoint()
            allow_image_input = await self.run_chat_task(
                {
                    "vault_name": vault.name,
                    "prompt": "Read an image through code_execution using delegate.",
                    "session_id": "code_execution_allow_image_input",
                    "tools": ["code_execution", "file_read"],
                    "model": "test",
                },
            )
            assert (
                allow_image_input["start_response"].status_code == 200
            ), "Image input composition task should start"
            assert (
                allow_image_input["terminal_event"].get("event") == "done"
            ), "Image input composition should succeed"
            allow_image_input_text = allow_image_input["text"]
            self.soft_assert(
                "failed:" not in allow_image_input_text.lower(),
                "Image input composition should not return a Monty failure",
            )
            image_events = self.events_since(checkpoint)
            self.assert_event_contains(
                image_events,
                name="authoring_direct_tool_completed",
                expected={
                    "workflow_id": "CodeExecutionVault/chat/code_execution_allow_image_input",
                    "tool": "delegate",
                },
            )

            current_case["name"] = "full_surface"
            checkpoint = self.event_checkpoint()
            full_surface = await self.run_chat_task(
                {
                    "vault_name": vault.name,
                    "prompt": "Exercise the full code_execution helper surface.",
                    "session_id": "code_execution_full_surface",
                    "tools": ["code_execution", "file_read", "file_write"],
                    "model": "test",
                },
            )
            assert (
                full_surface["start_response"].status_code == 200
            ), "Full helper-surface task should start"
            assert (
                full_surface["terminal_event"].get("event") == "done"
            ), "Full helper-surface run should succeed"
            full_surface_text = full_surface["text"]
            self.soft_assert(
                "failed:" not in full_surface_text.lower(),
                "Full helper-surface run should not return a Monty failure",
            )
            full_surface_events = self.events_since(checkpoint)
            self.assert_event_contains(
                full_surface_events,
                name="authoring_parse_markdown_completed",
                expected={
                    "workflow_id": "CodeExecutionVault/chat/code_execution_full_surface",
                    "heading_count": 2,
                    "section_count": 2,
                    "code_block_count": 1,
                    "image_count": 1,
                },
            )
            self.assert_event_contains(
                full_surface_events,
                name="authoring_read_cache_completed",
                expected={
                    "workflow_id": "CodeExecutionVault/chat/code_execution_full_surface",
                    "ref": "tool/web_extract/call_seeded_full_surface",
                    "exists": True,
                },
            )
            self.assert_event_contains(
                full_surface_events,
                name="authoring_pending_files_completed",
                expected={
                    "workflow_id": "CodeExecutionVault/chat/code_execution_full_surface",
                    "completed_count": 1,
                },
            )
            self.assert_event_contains(
                full_surface_events,
                name="authoring_assemble_context_completed",
                expected={
                    "workflow_id": "CodeExecutionVault/chat/code_execution_full_surface",
                    "message_count": 1,
                    "instruction_count": 1,
                },
            )
            self.assert_event_contains(
                full_surface_events,
                name="authoring_direct_tool_completed",
                expected={
                    "workflow_id": "CodeExecutionVault/chat/code_execution_full_surface",
                    "tool": "file_read",
                },
            )
            self.assert_event_contains(
                full_surface_events,
                name="authoring_retrieve_history_completed",
                expected={
                    "workflow_id": "CodeExecutionVault/chat/code_execution_full_surface",
                    "item_count": 0,
                },
            )
            self.assert_event_contains(
                full_surface_events,
                name="authoring_direct_tool_completed",
                expected={
                    "workflow_id": "CodeExecutionVault/chat/code_execution_full_surface",
                    "tool": "delegate",
                },
            )
            self.assert_event_contains(
                full_surface_events,
                name="authoring_finish_requested",
                expected={
                    "workflow_id": "CodeExecutionVault/chat/code_execution_full_surface",
                    "status": "completed",
                    "reason": "full-surface-ok",
                },
            )
            self.assert_event_contains(
                full_surface_events,
                name="authoring_monty_execution_completed",
                expected={
                    "workflow_id": "CodeExecutionVault/chat/code_execution_full_surface",
                    "status": "completed",
                    "reason": "full-surface-ok",
                },
            )

            derived_path = vault / "notes" / "derived.md"
            assert derived_path.exists(), "Allowed write should create the derived file"
            self.soft_assert_equal(
                derived_path.read_text(encoding="utf-8"),
                "DERIVED_RESULT",
                "Derived file should preserve the written content",
            )
            full_surface_path = vault / "notes" / "full-surface.md"
            assert (
                full_surface_path.exists()
            ), "Full helper-surface run should write a file"
            self.soft_assert(
                bool(full_surface_path.read_text(encoding="utf-8").strip()),
                "Full helper-surface run should write non-empty delegated output",
            )
        finally:
            chat_executor._prepare_agent_config = original_prepare_agent_config
            await self.stop_system()
            self.teardown_scenario()


STRUCTURED_NOTE = """---
name: Skill Example
description: Deterministic markdown structure fixture
---

# Overview

Intro text for the structured note.

## AI In Fiction

This section is about fictional AI examples.

```python
print("hello")
```

![Fixture image](../images/test_image.jpg)
"""
