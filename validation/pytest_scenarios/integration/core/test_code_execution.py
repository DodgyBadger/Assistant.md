"""Chat code_execution helper outputs, native results, streaming and file effects."""

from __future__ import annotations

from datetime import datetime

import pytest

from validation.pytest_scenarios.application import Application
from validation.pytest_scenarios.support.code_tool_chat import (
    STRUCTURED_NOTE,
    call_code_tool,
    code_tool_provider,
    configure_provider,
)
from validation.pytest_scenarios.support.file_tools import copy_image_fixture

pytestmark = pytest.mark.scenario


async def test_code_execution_chat_helper_surface(app: Application) -> None:
    vault = app.vault("CodeExecutionVault")
    app.write(vault, "notes/structured.md", STRUCTURED_NOTE)
    app.write(vault, "notes/blocked.md", "BLOCKED_CONTENT")
    app.write(vault, "tasks/pending-one.md", "PENDING_ONE")
    copy_image_fixture(vault)
    with code_tool_provider() as server:
        await app.start()
        from core.authoring.cache import upsert_cache_artifact

        configure_provider(app, server)
        case_name = "allow_memory_read"
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
        allow_read = await call_code_tool(
            app,
            server,
            vault,
            case_name,
            {
                "vault_name": vault.name,
                "prompt": "Read session history through code_execution.",
                "session_id": "code_execution_allow_read",
                "model": "code-tool-model",
            },
        )
        assert (
            allow_read["start_response"].status_code == 200
        ), "Memory read task should start"
        assert (
            allow_read["terminal_event"].get("event") == "done"
        ), "Memory read should succeed"
        allow_text = allow_read["text"]
        assert (
            "failed:" not in allow_text.lower()
        ), "Memory read should not return a Monty failure"
        assert any(
            digit in allow_text for digit in ("0", "1")
        ), "Memory read should return a structured item count"
        case_name = "allow_cache_read"
        allow_cache_read = await call_code_tool(
            app,
            server,
            vault,
            case_name,
            {
                "vault_name": vault.name,
                "prompt": "Read a cached oversized tool artifact through code_execution.",
                "session_id": "code_execution_allow_cache_read",
                "model": "code-tool-model",
            },
        )
        assert (
            allow_cache_read["start_response"].status_code == 200
        ), "Cache read task should start"
        assert (
            allow_cache_read["terminal_event"].get("event") == "done"
        ), "Cache read should succeed"
        allow_cache_read_text = allow_cache_read["text"]
        assert (
            "SEEDED_CACHE_CONTENT" in allow_cache_read_text
        ), "Cache read should return the cached artifact content"
        case_name = "discovery"
        discovery = await call_code_tool(
            app,
            server,
            vault,
            case_name,
            {
                "vault_name": vault.name,
                "prompt": "Inspect code_execution with no arguments first.",
                "session_id": "code_execution_discovery",
                "model": "code-tool-model",
            },
        )
        assert (
            discovery["start_response"].status_code == 200
        ), "No-arg code_execution task should start"
        assert (
            discovery["terminal_event"].get("event") == "done"
        ), "No-arg code_execution discovery should succeed"
        discovery_text = discovery["text"]
        assert bool(
            discovery_text.strip()
        ), "No-arg code_execution should return a non-empty discovery response"
        case_name = "allow_file_read"
        allow_file_read = await call_code_tool(
            app,
            server,
            vault,
            case_name,
            {
                "vault_name": vault.name,
                "prompt": "Read a vault file through code_execution.",
                "session_id": "code_execution_allow_file_read",
                "model": "code-tool-model",
            },
        )
        assert (
            allow_file_read["start_response"].status_code == 200
        ), "Vault file read task should start"
        assert (
            allow_file_read["terminal_event"].get("event") == "done"
        ), "Vault file read should succeed"
        allow_file_read_text = allow_file_read["text"]
        assert (
            "BLOCKED_CONTENT" in allow_file_read_text
        ), "Vault file read should return the file content"
        case_name = "dynamic_attribute_helpers"
        dynamic_attribute_helpers = await call_code_tool(
            app,
            server,
            vault,
            case_name,
            {
                "vault_name": vault.name,
                "prompt": "Exercise dynamic Monty attribute helpers.",
                "session_id": "code_execution_dynamic_attribute_helpers",
                "model": "code-tool-model",
            },
        )
        assert (
            dynamic_attribute_helpers["start_response"].status_code == 200
        ), "Dynamic attribute helper task should start"
        assert (
            dynamic_attribute_helpers["terminal_event"].get("event") == "done"
        ), "Dynamic attribute helper run should complete"
        dynamic_attribute_helpers_text = dynamic_attribute_helpers["text"]
        assert (
            "true" in dynamic_attribute_helpers_text.lower()
        ), "code_execution should support hasattr/getattr on helper result objects"
        from pydantic_ai.messages import ModelRequest, UserPromptPart

        from core.chat.chat_store import ChatStore
        from core.identity import LOCAL_USER_PRINCIPAL_ID

        history_store = ChatStore()
        history_store.ensure_session(
            "code_execution_registered_values",
            vault.name,
            owner_principal_id=LOCAL_USER_PRINCIPAL_ID,
        )
        history_store.add_messages(
            "code_execution_registered_values",
            vault.name,
            [ModelRequest(parts=[UserPromptPart("HISTORY_REGISTERED_VALUE")])],
        )
        case_name = "registered_values"
        registered_values = await call_code_tool(
            app,
            server,
            vault,
            case_name,
            {
                "vault_name": vault.name,
                "prompt": "Read registered date, markdown and prior history values.",
                "session_id": "code_execution_registered_values",
                "model": "code-tool-model",
            },
        )
        assert registered_values["terminal_event"].get("event") == "done"
        assert f"{datetime.now().year}:AI In Fiction" in registered_values["text"]
        assert "HISTORY_REGISTERED_VALUE" in registered_values["text"]
        case_name = "private_method"
        private_method = await call_code_tool(
            app,
            server,
            vault,
            case_name,
            {
                "vault_name": vault.name,
                "prompt": "Attempt to call a private date method.",
                "session_id": "code_execution_private_method",
                "model": "code-tool-model",
            },
        )
        assert private_method["terminal_event"].get("event") == "done"
        app.events.require(
            "authoring_monty_execution_failed",
            fields={
                "workflow_id": "CodeExecutionVault/chat/code_execution_private_method"
            },
        )
        case_name = "streaming_detail"
        streaming_detail = await call_code_tool(
            app,
            server,
            vault,
            case_name,
            {
                "vault_name": vault.name,
                "prompt": "Stream code_execution detail payloads.",
                "session_id": "code_execution_streaming_detail",
                "model": "code-tool-model",
            },
        )
        assert (
            streaming_detail["start_response"].status_code == 200
        ), "Streaming detail task should start"
        assert (
            streaming_detail["terminal_event"].get("event") == "done"
        ), "Streaming detail run should succeed"
        streaming_events = streaming_detail["events"]
        assert any(
            event.get("event") == "tool_call_started"
            and event.get("tool_name") == "code_execution"
            and ("arguments" not in event)
            and ("arguments_detail" not in event)
            for event in streaming_events
        ), "Streaming tool start events should omit arguments"
        assert any(
            event.get("event") == "tool_call_finished"
            and event.get("tool_name") == "code_execution"
            and isinstance(event.get("token_count"), int)
            and (event["token_count"] >= 0)
            and ("result" not in event)
            and ("result_detail" not in event)
            and ("result_metadata" not in event)
            and ("artifact_ref" not in event)
            for event in streaming_events
        ), "Streaming tool finish events should expose only safe result metadata"
        case_name = "allow_write"
        allow_write = await call_code_tool(
            app,
            server,
            vault,
            case_name,
            {
                "vault_name": vault.name,
                "prompt": "Write a derived cache artifact through code_execution.",
                "session_id": "code_execution_allow_write",
                "model": "code-tool-model",
            },
        )
        assert (
            allow_write["start_response"].status_code == 200
        ), "Allowed cache write task should start"
        assert (
            allow_write["terminal_event"].get("event") == "done"
        ), "Allowed cache write should succeed"
        write_text = allow_write["text"]
        assert (
            "WRITE_OK" in write_text
        ), "Allowed cache write should return the snippet result"
        case_name = "allow_image_input"
        checkpoint = app.events.checkpoint()
        allow_image_input = await call_code_tool(
            app,
            server,
            vault,
            case_name,
            {
                "vault_name": vault.name,
                "prompt": "Read an image through code_execution using delegate.",
                "session_id": "code_execution_allow_image_input",
                "model": "code-tool-model",
            },
        )
        assert (
            allow_image_input["start_response"].status_code == 200
        ), "Image input composition task should start"
        assert (
            allow_image_input["terminal_event"].get("event") == "done"
        ), "Image input composition should succeed"
        allow_image_input_text = allow_image_input["text"]
        assert (
            "failed:" not in allow_image_input_text.lower()
        ), "Image input composition should not return a Monty failure"
        app.events.require(
            "authoring_direct_tool_completed",
            fields={
                "workflow_id": "CodeExecutionVault/chat/code_execution_allow_image_input",
                "tool": "delegate",
            },
            after=checkpoint,
        )
        case_name = "full_surface"
        checkpoint = app.events.checkpoint()
        full_surface = await call_code_tool(
            app,
            server,
            vault,
            case_name,
            {
                "vault_name": vault.name,
                "prompt": "Exercise the full code_execution helper surface.",
                "session_id": "code_execution_full_surface",
                "model": "code-tool-model",
            },
        )
        assert (
            full_surface["start_response"].status_code == 200
        ), "Full helper-surface task should start"
        assert (
            full_surface["terminal_event"].get("event") == "done"
        ), "Full helper-surface run should succeed"
        full_surface_text = full_surface["text"]
        assert (
            "failed:" not in full_surface_text.lower()
        ), "Full helper-surface run should not return a Monty failure"
        app.events.require(
            "authoring_parse_markdown_completed",
            fields={
                "workflow_id": "CodeExecutionVault/chat/code_execution_full_surface",
                "heading_count": 2,
                "section_count": 2,
                "code_block_count": 1,
                "image_count": 1,
            },
            after=checkpoint,
        )
        app.events.require(
            "authoring_read_cache_completed",
            fields={
                "workflow_id": "CodeExecutionVault/chat/code_execution_full_surface",
                "ref": "tool/web_extract/call_seeded_full_surface",
                "exists": True,
            },
            after=checkpoint,
        )
        app.events.require(
            "authoring_pending_files_completed",
            fields={
                "workflow_id": "CodeExecutionVault/chat/code_execution_full_surface",
                "completed_count": 1,
            },
            after=checkpoint,
        )
        app.events.require(
            "authoring_assemble_context_completed",
            fields={
                "workflow_id": "CodeExecutionVault/chat/code_execution_full_surface",
                "message_count": 1,
                "instruction_count": 1,
            },
            after=checkpoint,
        )
        app.events.require(
            "authoring_direct_tool_completed",
            fields={
                "workflow_id": "CodeExecutionVault/chat/code_execution_full_surface",
                "tool": "file_read",
            },
            after=checkpoint,
        )
        app.events.require(
            "authoring_retrieve_history_completed",
            fields={
                "workflow_id": "CodeExecutionVault/chat/code_execution_full_surface",
                "item_count": 0,
            },
            after=checkpoint,
        )
        app.events.require(
            "authoring_direct_tool_completed",
            fields={
                "workflow_id": "CodeExecutionVault/chat/code_execution_full_surface",
                "tool": "delegate",
            },
            after=checkpoint,
        )
        app.events.require(
            "authoring_finish_requested",
            fields={
                "workflow_id": "CodeExecutionVault/chat/code_execution_full_surface",
                "status": "completed",
                "reason": "full-surface-ok",
            },
            after=checkpoint,
        )
        app.events.require(
            "authoring_monty_execution_completed",
            fields={
                "workflow_id": "CodeExecutionVault/chat/code_execution_full_surface",
                "status": "completed",
                "reason": "full-surface-ok",
            },
            after=checkpoint,
        )
        derived_path = vault / "notes" / "derived.md"
        assert derived_path.exists(), "Allowed write should create the derived file"
        assert (
            derived_path.read_text(encoding="utf-8") == "DERIVED_RESULT"
        ), "Derived file should preserve the written content"
        full_surface_path = vault / "notes" / "full-surface.md"
        assert full_surface_path.exists(), "Full helper-surface run should write a file"
        assert bool(
            full_surface_path.read_text(encoding="utf-8").strip()
        ), "Full helper-surface run should write non-empty delegated output"
