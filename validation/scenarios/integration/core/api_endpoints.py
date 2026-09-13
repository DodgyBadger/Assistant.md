"""
Integration scenario that exercises every documented API endpoint using the
validation harness' shared FastAPI TestClient.
"""

import asyncio
import base64
import json
import re
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlparse

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

_direct_run_root: tempfile.TemporaryDirectory[str] | None = None
if __name__ == "__main__":
    from core.runtime.paths import set_bootstrap_roots

    _direct_run_root = tempfile.TemporaryDirectory(prefix="assistantmd-api-endpoints-")
    direct_root = Path(_direct_run_root.name)
    data_root = direct_root / "data"
    bootstrap_system_root = direct_root / "system"
    data_root.mkdir()
    bootstrap_system_root.mkdir()
    set_bootstrap_roots(data_root=data_root, system_root=bootstrap_system_root)

from core.identity import LOCAL_USER_AUTHORITY  # noqa: E402
from core.llm.openai_oauth import (  # noqa: E402
    OPENAI_OAUTH_CLIENT_ID,
    OPENAI_OAUTH_LOOPBACK_REDIRECT_URI,
    OPENAI_OAUTH_ORIGINATOR,
    OPENAI_OAUTH_SCOPE,
    OpenAIOAuthDeviceCodeResult,
    OpenAIOAuthDevicePollResult,
    OpenAIOAuthTokenResult,
    StaticOpenAIOAuthTokenAdapter,
    set_openai_oauth_token_adapter,
)
from core.mcp import MCPConnectionTestResult  # noqa: E402
from core.runtime.execution_tasks import ExecutionTaskSource  # noqa: E402
from core.runtime.state import get_runtime_context  # noqa: E402
from validation.core.base_scenario import BaseScenario  # noqa: E402


class ApiEndpointsScenario(BaseScenario):
    """Validate core REST endpoints end-to-end using real runtime context."""

    async def test_scenario(self):
        try:
            await self._run_api_endpoint_checks()
        finally:
            set_openai_oauth_token_adapter(None)
            if self._system_controller and self._system_controller.is_running:
                await self.stop_system()

    async def _run_api_endpoint_checks(self):
        vault = self.create_vault("IntegrationApiVault")

        # Seed a minimal step workflow for execution and status checks
        self.create_file(
            vault,
            "AssistantMD/Authoring/status_probe.md",
            STATUS_PROBE_WORKFLOW,
        )
        self.create_file(
            vault,
            "AssistantMD/Authoring/skipped_probe.md",
            SKIPPED_PROBE_WORKFLOW,
        )
        system_workflow_path = (
            self._get_system_controller()._system_root
            / "Authoring"
            / "api-system-probe.md"
        )
        system_workflow_path.parent.mkdir(parents=True, exist_ok=True)
        system_workflow_path.write_text(API_SYSTEM_PROBE_WORKFLOW, encoding="utf-8")

        # Health prior to runtime bootstrap should indicate startup state
        pre_health = self.call_api("/api/health")
        assert (
            pre_health.status_code == 503
        ), "Health reports starting state before runtime boots"

        await self.start_system()

        # Core system endpoints
        health = self.call_api("/api/health")
        assert health.status_code == 200, "Health endpoint reports healthy"

        status_response = self.call_api("/api/status")
        assert status_response.status_code == 200, "Status endpoint succeeds"
        status_payload = status_response.json()
        assert status_payload.get("total_vaults") == 1, "One vault discovered"
        assert status_payload.get("total_workflows") == 2, "Seeded workflows counted"

        activity = self.call_api("/api/system/activity-log")
        assert activity.status_code == 200, "Activity log fetch succeeds"

        settings = self.call_api("/api/system/settings")
        assert settings.status_code == 200, "Settings fetch succeeds"
        settings_payload = settings.json()
        settings_content = yaml.safe_load(settings_payload["content"]) or {}
        settings_content.setdefault("settings", {}).setdefault(
            "default_model",
            {
                "value": "test",
                "description": "Validation default model.",
                "category": "Models",
                "restart_required": False,
            },
        )
        settings_content["settings"]["default_model"]["value"] = "test"
        settings_content.setdefault("models", {})["test"] = {
            "provider": "test",
            "model_string": "test",
            "capabilities": ["text"],
            "description": "Validation deterministic chat model",
            "user_editable": True,
        }
        settings_content.setdefault("providers", {})["test"] = {
            "api_key": None,
            "base_url": None,
            "user_editable": False,
        }
        update_settings = self.call_api(
            "/api/system/settings",
            method="PUT",
            data={"content": yaml.safe_dump(settings_content, sort_keys=False)},
        )
        assert update_settings.status_code == 200, "Settings update round-trips"

        general_settings = self.call_api("/api/system/settings/general")
        assert general_settings.status_code == 200, "General settings load"
        general_payload = general_settings.json()
        if general_payload:
            first_setting = general_payload[0]
            update_setting = self.call_api(
                f"/api/system/settings/general/{first_setting['key']}",
                method="PUT",
                data={"value": first_setting["value"]},
            )
            assert (
                update_setting.status_code == 200
            ), "General setting update acknowledged"

        # Model configuration lifecycle (create + delete)
        models_resp = self.call_api("/api/system/models")
        assert models_resp.status_code == 200, "Model listing succeeds"
        models_payload = models_resp.json()
        base_model = models_payload[0]
        model_alias = "validation-test-model"
        created_model = self.call_api(
            f"/api/system/models/{model_alias}",
            method="PUT",
            data={
                "provider": base_model["provider"],
                "model_string": base_model["model_string"],
                "description": "Validation alias for API coverage",
            },
        )
        assert created_model.status_code == 200, "Model alias creation works"
        removed_model = self.call_api(
            f"/api/system/models/{model_alias}", method="DELETE"
        )
        assert removed_model.status_code == 200, "Model alias deletion works"

        # Provider configuration lifecycle (create + delete)
        providers_resp = self.call_api("/api/system/providers")
        assert providers_resp.status_code == 200, "Provider listing succeeds"
        providers_payload = providers_resp.json()
        openai_provider = next(
            provider for provider in providers_payload if provider["name"] == "openai"
        )
        assert (
            openai_provider["user_editable"] is False
        ), "Built-in OpenAI provider is protected by default"
        assert (
            openai_provider["configured_auth_mode"] == "api_key"
        ), "OpenAI provider defaults to API-key auth mode"
        assert (
            openai_provider["effective_auth_mode"] == "api_key"
        ), "OpenAI provider runs API-key auth while OAuth is disabled"
        assert (
            openai_provider["oauth_enabled"] is False
        ), "OpenAI OAuth is globally disabled by default"
        assert (
            openai_provider["oauth_status"] == "disabled"
        ), "OpenAI OAuth status reports the global disable override"

        allow_openai_edit = self.call_api(
            "/api/system/settings/general/editable_builtin_providers",
            method="PUT",
            data={"value": '["openai"]'},
        )
        assert (
            allow_openai_edit.status_code == 200
        ), "Built-in provider edit allowlist can be updated"
        updated_openai_provider = self.call_api(
            "/api/system/providers/openai",
            method="PUT",
            data={
                "auth_mode": "oauth",
                "oauth_api_key_fallback_enabled": True,
            },
        )
        assert (
            updated_openai_provider.status_code == 200
        ), "Allowlisted OpenAI provider accepts auth metadata updates"
        updated_openai_payload = updated_openai_provider.json()
        assert (
            updated_openai_payload["configured_auth_mode"] == "oauth"
        ), "OpenAI provider records OAuth as the configured auth mode"
        assert (
            updated_openai_payload["effective_auth_mode"] == "api_key"
        ), "Global OAuth disable still forces API-key effective mode"
        assert (
            updated_openai_payload["oauth_api_key_fallback_enabled"] is True
        ), "OpenAI provider records explicit API-key fallback opt-in"

        oauth_enable = self.call_api(
            "/api/system/settings/general/openai_oauth_enabled",
            method="PUT",
            data={"value": "true"},
        )
        assert oauth_enable.status_code == 200, "OpenAI OAuth can be enabled"
        token_expiry = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        set_openai_oauth_token_adapter(
            StaticOpenAIOAuthTokenAdapter(
                OpenAIOAuthTokenResult(
                    access_token="validation-access-token",
                    refresh_token="validation-refresh-token",
                    expires_at=token_expiry,
                    account_id="validation-account",
                )
            )
        )
        oauth_start = self.call_api(
            "/api/system/providers/openai/oauth/start",
            method="POST",
            data={},
        )
        assert oauth_start.status_code == 200, "OpenAI OAuth start succeeds"
        oauth_start_payload = oauth_start.json()
        assert oauth_start_payload["auth_url"], "OAuth start returns auth URL"
        assert re.fullmatch(
            r"[0-9a-f]{32}", oauth_start_payload["state"]
        ), "OAuth start returns OpenClaw-compatible hex state"
        assert (
            oauth_start_payload["redirect_uri"] == OPENAI_OAUTH_LOOPBACK_REDIRECT_URI
        ), "OAuth start defaults to the Codex loopback redirect URI"
        auth_url = urlparse(oauth_start_payload["auth_url"])
        auth_query = parse_qs(auth_url.query)
        assert auth_query.get("client_id") == [
            OPENAI_OAUTH_CLIENT_ID
        ], "OpenAI OAuth start uses Codex client id"
        assert auth_query.get("scope") == [
            OPENAI_OAUTH_SCOPE
        ], "OpenAI OAuth start uses Codex OAuth scope"
        assert auth_query.get("originator") == [
            OPENAI_OAUTH_ORIGINATOR
        ], "OpenAI OAuth start identifies the Codex originator"

        pasted_redirect = (
            f"{oauth_start_payload['redirect_uri']}?code=validation-code"
            f"&state={oauth_start_payload['state']}"
        )
        oauth_complete = self.call_api(
            "/api/system/providers/openai/oauth/complete",
            method="POST",
            data={"redirect_url": pasted_redirect},
        )
        assert (
            oauth_complete.status_code == 200
        ), "OpenAI OAuth manual completion succeeds with fake adapter"
        oauth_complete_payload = oauth_complete.json()
        assert (
            oauth_complete_payload["oauth_status"] == "connected"
        ), "OpenAI OAuth status reports connected after completion"
        assert (
            oauth_complete_payload["oauth_account_id"] == "validation-account"
        ), "OpenAI OAuth status exposes sanitized account metadata"
        oauth_models = self.call_api("/api/system/models")
        assert (
            oauth_models.status_code == 200
        ), "Model listing succeeds after OAuth completion"
        oauth_gpt = next(
            model for model in oauth_models.json() if model["name"] == "gpt"
        )
        assert (
            oauth_gpt["available"] is True
        ), "OpenAI model is available while OAuth is connected"

        oauth_disconnect = self.call_api(
            "/api/system/providers/openai/oauth",
            method="DELETE",
        )
        assert oauth_disconnect.status_code == 200, "OpenAI OAuth disconnect succeeds"
        oauth_status = self.call_api("/api/system/providers/openai/oauth/status")
        assert oauth_status.status_code == 200, "OpenAI OAuth status endpoint succeeds"
        assert (
            oauth_status.json()["oauth_status"] == "disconnected"
        ), "OpenAI OAuth status reports disconnected after disconnect"
        disconnected_models = self.call_api("/api/system/models")
        assert (
            disconnected_models.status_code == 200
        ), "Model listing succeeds after OAuth disconnect"
        disconnected_gpt = next(
            model for model in disconnected_models.json() if model["name"] == "gpt"
        )
        assert (
            disconnected_gpt["available"] is False
        ), "OpenAI model is unavailable after disconnect without API-key fallback"

        set_openai_oauth_token_adapter(
            StaticOpenAIOAuthTokenAdapter(
                OpenAIOAuthTokenResult(
                    access_token="validation-device-access-token",
                    refresh_token="validation-device-refresh-token",
                    expires_at=token_expiry,
                    account_id="validation-device-account",
                ),
                device_code_result=OpenAIOAuthDeviceCodeResult(
                    device_auth_id="validation-device-auth",
                    user_code="VALIDATION-DEVICE",
                    poll_interval_seconds=2,
                ),
                device_poll_result=OpenAIOAuthDevicePollResult(status="pending"),
            )
        )
        device_start = self.call_api(
            "/api/system/providers/openai/oauth/device/start",
            method="POST",
        )
        assert (
            device_start.status_code == 200
        ), "OpenAI OAuth device-code start succeeds"
        device_start_payload = device_start.json()
        assert (
            device_start_payload["user_code"] == "VALIDATION-DEVICE"
        ), "Device-code start returns the user code"
        assert (
            device_start_payload["poll_interval_seconds"] == 2
        ), "Device-code start returns the polling interval"
        device_status = self.call_api("/api/system/providers/openai/oauth/status")
        assert (
            device_status.status_code == 200
        ), "OpenAI OAuth status succeeds after device-code start"
        device_status_payload = device_status.json()
        assert (
            device_status_payload["oauth_status"] == "pending"
        ), "OpenAI OAuth status reports pending device-code auth"
        assert (
            device_status_payload["oauth_pending_flow"] == "device_code"
        ), "OpenAI OAuth status exposes the pending device-code flow"
        assert (
            device_status_payload["oauth_device_user_code"] == "VALIDATION-DEVICE"
        ), "OpenAI OAuth status exposes the device code for the provider panel"
        pending_device_check = self.call_api(
            "/api/system/providers/openai/oauth/device/check",
            method="POST",
        )
        assert (
            pending_device_check.status_code == 200
        ), "OpenAI OAuth pending device-code check succeeds"
        pending_device_payload = pending_device_check.json()
        assert (
            pending_device_payload["status"] == "pending"
        ), "Pending device-code check remains pending"
        assert (
            pending_device_payload["provider"]["oauth_status"] == "pending"
        ), "Pending device-code check does not persist a token"

        set_openai_oauth_token_adapter(
            StaticOpenAIOAuthTokenAdapter(
                OpenAIOAuthTokenResult(
                    access_token="validation-device-access-token",
                    refresh_token="validation-device-refresh-token",
                    expires_at=token_expiry,
                    account_id="validation-device-account",
                ),
                device_poll_result=OpenAIOAuthDevicePollResult(
                    status="authorized",
                    authorization_code="validation-device-authorization-code",
                    code_verifier="validation-device-code-verifier",
                ),
            )
        )
        authorized_device_check = self.call_api(
            "/api/system/providers/openai/oauth/device/check",
            method="POST",
        )
        assert (
            authorized_device_check.status_code == 200
        ), "OpenAI OAuth authorized device-code check succeeds"
        authorized_device_payload = authorized_device_check.json()
        assert (
            authorized_device_payload["status"] == "connected"
        ), "Authorized device-code check reports connected"
        assert (
            authorized_device_payload["provider"]["oauth_status"] == "connected"
        ), "Authorized device-code check persists OAuth token state"
        assert (
            authorized_device_payload["provider"]["oauth_account_id"]
            == "validation-device-account"
        ), "Authorized device-code check exposes sanitized account metadata"
        api_key_mode_with_oauth = self.call_api(
            "/api/system/providers/openai",
            method="PUT",
            data={
                "auth_mode": "api_key",
                "oauth_api_key_fallback_enabled": False,
            },
        )
        assert (
            api_key_mode_with_oauth.status_code == 200
        ), "OpenAI provider can switch back to API-key mode while OAuth is connected"
        api_key_mode_payload = api_key_mode_with_oauth.json()
        assert (
            api_key_mode_payload["configured_auth_mode"] == "api_key"
        ), "OpenAI provider records API-key mode"
        assert (
            api_key_mode_payload["effective_auth_mode"] == "oauth"
        ), "OpenAI provider uses connected OAuth when API-key mode has no key"
        api_key_mode_models = self.call_api("/api/system/models")
        assert (
            api_key_mode_models.status_code == 200
        ), "Model listing succeeds with API-key mode and connected OAuth"
        api_key_mode_gpt = next(
            model for model in api_key_mode_models.json() if model["name"] == "gpt"
        )
        assert (
            api_key_mode_gpt["available"] is True
        ), "OpenAI model remains available when OAuth is connected but API key is absent"

        oauth_disconnect = self.call_api(
            "/api/system/providers/openai/oauth",
            method="DELETE",
        )
        assert (
            oauth_disconnect.status_code == 200
        ), "OpenAI OAuth disconnect succeeds after device-code auth"
        set_openai_oauth_token_adapter(None)

        provider_alias = "validation-provider"
        created_provider = self.call_api(
            f"/api/system/providers/{provider_alias}",
            method="PUT",
            data={
                "api_key": "VALIDATION_PROVIDER_KEY",
                "base_url": "VALIDATION_PROVIDER_BASE_URL",
            },
        )
        assert created_provider.status_code == 200, "Provider creation acknowledged"
        removed_provider = self.call_api(
            f"/api/system/providers/{provider_alias}", method="DELETE"
        )
        assert removed_provider.status_code == 200, "Provider deletion acknowledged"

        # Secrets endpoint lifecycle: list, set, clear (scenario-local overlay)
        secrets_list = self.call_api("/api/system/secrets")
        assert secrets_list.status_code == 200, "Secrets listing succeeds"
        secrets_payload = secrets_list.json()
        assert isinstance(secrets_payload, list), "Secrets list is returned"

        secret_update = self.call_api(
            "/api/system/secrets",
            method="PUT",
            data={"name": "VALIDATION_TEMP_SECRET", "value": "123"},
        )
        assert secret_update.status_code == 200, "Secret update succeeds"

        updated_secrets = self.call_api("/api/system/secrets")
        assert updated_secrets.status_code == 200, "Secrets refresh succeeds"
        assert any(
            entry["name"] == "VALIDATION_TEMP_SECRET" and entry["has_value"]
            for entry in updated_secrets.json()
        ), "Updated secret reported with value"

        internal_oauth_secret = self.call_api(
            "/api/system/secrets",
            method="PUT",
            data={
                "name": "OPENAI_OAUTH_TOKEN_STATE",
                "value": json.dumps(
                    {
                        "access_token": "validation-token",
                        "account_id": "validation-account",
                    }
                ),
            },
        )
        assert (
            internal_oauth_secret.status_code == 200
        ), "Internal OAuth secret can be persisted by the secrets store"
        oauth_hidden_secrets = self.call_api("/api/system/secrets")
        assert (
            oauth_hidden_secrets.status_code == 200
        ), "Secrets list still succeeds with internal OAuth state"
        assert not any(
            entry["name"] == "OPENAI_OAUTH_TOKEN_STATE"
            for entry in oauth_hidden_secrets.json()
        ), "Internal OpenAI OAuth token state is hidden from generic secrets"
        assert not any(
            entry["name"] == "OPENAI_OAUTH_PENDING_STATE"
            for entry in oauth_hidden_secrets.json()
        ), "Internal OpenAI OAuth pending state is hidden from generic secrets"

        secret_clear = self.call_api(
            "/api/system/secrets",
            method="PUT",
            data={"name": "VALIDATION_TEMP_SECRET", "value": ""},
        )
        assert secret_clear.status_code == 200, "Secret cleared successfully"

        cleared_secrets = self.call_api("/api/system/secrets")
        assert not any(
            entry["name"] == "VALIDATION_TEMP_SECRET" and entry["has_value"]
            for entry in cleared_secrets.json()
        ), "Secret list no longer reports a stored value"

        # MCP connection management exposes metadata and credential presence only.
        rejected_owner = self.call_api(
            "/api/system/mcp/connections",
            method="POST",
            data={
                "display_name": "Injected owner",
                "url": "https://example.com/mcp",
                "owner_principal_id": "foreign-user",
            },
        )
        assert rejected_owner.status_code == 422, "MCP owner injection is rejected"

        mcp_create = await self.call_api_async(
            "/api/system/mcp/connections",
            method="POST",
            data={
                "display_name": "Validation MCP",
                "url": "https://example.com/mcp",
                "transport": "streamable_http",
                "auth_mode": "bearer",
                "enabled": True,
                "allow_private_http": True,
                "allowed_tools": ["search"],
                "credential": "validation-mcp-token",
            },
        )
        assert mcp_create.status_code == 200, "MCP connection creation succeeds"
        mcp_payload = mcp_create.json()
        mcp_id = mcp_payload["connection_id"]
        assert mcp_payload["slug"] == "validation-mcp", "MCP slug is deterministic"
        assert (
            mcp_payload["credential_present"] is True
        ), "Credential presence is reported"
        assert "credential" not in mcp_payload, "Credential value is write-only"
        assert (
            mcp_payload["allow_private_http"] is True
        ), "Private HTTP acknowledgement is returned without weakening other connections"

        mcp_list = self.call_api("/api/system/mcp/connections")
        assert mcp_list.status_code == 200, "MCP connection listing succeeds"
        assert [entry["connection_id"] for entry in mcp_list.json()] == [
            mcp_id
        ], "MCP list contains only the created current-user connection"

        with patch(
            "core.mcp.manager.MCPConnectionManager.test_connection",
            new=AsyncMock(
                return_value=MCPConnectionTestResult(
                    status="ready",
                    ready=True,
                    tool_count=2,
                    tool_names=("search", "read"),
                    message="Connected successfully and discovered 2 available MCP tool(s).",
                )
            ),
        ):
            mcp_test = self.call_api(
                f"/api/system/mcp/connections/{mcp_id}/test",
                method="POST",
            )
        assert mcp_test.status_code == 200, "MCP test contract is available"
        assert (
            mcp_test.json()["status"] == "ready"
            and mcp_test.json()["ready"] is True
            and mcp_test.json()["tool_names"] == ["search", "read"]
        ), "MCP test reports sanitized readiness and discovered tool names"

        mcp_update = await self.call_api_async(
            f"/api/system/mcp/connections/{mcp_id}",
            method="PUT",
            data={
                "display_name": "Renamed Validation MCP",
                "url": "https://example.com/mcp",
                "transport": "sse",
                "auth_mode": "bearer",
                "header_name": None,
                "enabled": False,
                "allow_private_http": False,
                "allowed_tools": None,
            },
        )
        assert mcp_update.status_code == 200, "MCP connection update succeeds"
        assert mcp_update.json()["slug"] == "validation-mcp", "MCP slug stays immutable"
        assert mcp_update.json()["allow_private_http"] is False

        mcp_clear = await self.call_api_async(
            f"/api/system/mcp/connections/{mcp_id}/credential",
            method="DELETE",
        )
        assert mcp_clear.status_code == 200, "MCP credential clear succeeds"
        assert mcp_clear.json()["credential_present"] is False

        mcp_delete = await self.call_api_async(
            f"/api/system/mcp/connections/{mcp_id}",
            method="DELETE",
        )
        assert mcp_delete.status_code == 200, "MCP connection deletion succeeds"
        mcp_missing = self.call_api(
            f"/api/system/mcp/connections/{mcp_id}/test",
            method="POST",
        )
        assert mcp_missing.status_code == 404, "Deleted MCP connections look absent"

        # Vault rescan should keep workflow counts stable
        rescan_response = self.call_api("/api/vaults/rescan", method="POST")
        assert rescan_response.status_code == 200, "Vault rescan succeeds"

        # Manual workflow execution
        execute_response = self.call_api(
            "/api/workflows/execute",
            method="POST",
            data={"global_id": f"{vault.name}/status_probe"},
        )
        assert execute_response.status_code == 200, "Manual workflow execution succeeds"
        execute_payload = execute_response.json()
        assert execute_payload.get("success") is True, "Workflow start reports success"
        assert execute_payload.get("task", {}).get(
            "task_id"
        ), "Workflow start returns a task id"

        await get_runtime_context().workflow_governor.execute_workflow(
            global_id=f"{vault.name}/status_probe",
            source=ExecutionTaskSource.API,
            authority=LOCAL_USER_AUTHORITY,
        )

        # Execution task status endpoints expose the completed workflow task.
        tasks_response = self.call_api("/api/tasks")
        assert tasks_response.status_code == 200, "Execution task listing succeeds"
        tasks_payload = tasks_response.json()
        workflow_tasks = [
            task
            for task in tasks_payload.get("tasks", [])
            if task.get("kind") == "workflow"
            and task.get("metadata", {}).get("workflow_id")
            == f"{vault.name}/status_probe"
        ]
        assert workflow_tasks, "Workflow execution should create a task snapshot"
        workflow_task = workflow_tasks[-1]
        assert (
            workflow_task.get("status") == "completed"
        ), "Workflow task should complete"
        assert (
            workflow_task.get("metadata", {}).get("workflow_result", {}).get("status")
            == "completed"
        ), "Workflow task metadata should include the terminal workflow result"

        task_detail = self.call_api(f"/api/tasks/{workflow_task['task_id']}")
        assert task_detail.status_code == 200, "Execution task detail succeeds"
        assert (
            task_detail.json().get("task_id") == workflow_task["task_id"]
        ), "Execution task detail returns the requested task"

        workflow_task_list = self.call_api(
            f"/api/workflows/tasks?vault_name={vault.name}"
        )
        assert workflow_task_list.status_code == 200, "Workflow task listing succeeds"
        assert any(
            task.get("task_id") == workflow_task["task_id"]
            for task in workflow_task_list.json().get("tasks", [])
        ), "Workflow task listing includes the completed task"

        terminal_cancel = self.call_api(
            f"/api/tasks/{workflow_task['task_id']}/cancel",
            method="POST",
        )
        assert terminal_cancel.status_code == 200, "Terminal task cancel is idempotent"
        assert (
            terminal_cancel.json().get("task", {}).get("status") == "completed"
        ), "Cancelling a terminal task should preserve terminal status"
        assert (
            terminal_cancel.json().get("cancelled") is False
        ), "Cancelling a completed task should not report an effective cancellation"

        skipped_execute = self.call_api(
            "/api/workflows/execute",
            method="POST",
            data={"global_id": f"{vault.name}/skipped_probe"},
        )
        assert skipped_execute.status_code == 200, "Skipped workflow execution succeeds"
        skipped_payload = skipped_execute.json()
        assert (
            skipped_payload.get("success") is True
        ), "Skipped workflow start reports success"
        assert skipped_payload.get("task", {}).get(
            "task_id"
        ), "Skipped workflow start returns a task id"
        await get_runtime_context().workflow_governor.execute_workflow(
            global_id=f"{vault.name}/skipped_probe",
            source=ExecutionTaskSource.API,
            authority=LOCAL_USER_AUTHORITY,
        )

        skipped_tasks_response = self.call_api("/api/tasks")
        assert (
            skipped_tasks_response.status_code == 200
        ), "Execution task listing succeeds after skipped workflow"
        skipped_workflow_tasks = [
            task
            for task in skipped_tasks_response.json().get("tasks", [])
            if task.get("kind") == "workflow"
            and task.get("metadata", {}).get("workflow_id")
            == f"{vault.name}/skipped_probe"
        ]
        assert skipped_workflow_tasks, "Skipped workflow should create a task snapshot"
        skipped_task = skipped_workflow_tasks[-1]
        assert (
            skipped_task.get("status") == "skipped"
        ), "Skipped workflow task should be skipped"
        assert (
            skipped_task.get("terminal_reason") == "status-probe-skipped"
        ), "Skipped workflow task should retain finish reason"

        system_checkpoint = self.event_checkpoint()
        system_execute = self.call_api(
            "/api/workflows/execute",
            method="POST",
            data={
                "global_id": "system/api-system-probe",
                "vault_name": vault.name,
            },
        )
        assert (
            system_execute.status_code == 200
        ), "Disabled system workflow manual execution starts"
        system_payload = system_execute.json()
        system_task_id = system_payload.get("task", {}).get("task_id")
        assert system_task_id, "System workflow start returns a task id"
        system_task = await self._wait_for_execution_task(system_task_id)
        self.soft_assert_equal(
            system_task.get("status"),
            "completed",
            "System workflow task should complete through the governor",
        )
        self.soft_assert_equal(
            system_task.get("label"),
            f"{vault.name}/system/api-system-probe",
            "System workflow API execution should normalize to a vault-scoped workflow id",
        )
        self.soft_assert_equal(
            system_task.get("metadata", {}).get("workflow_id"),
            f"{vault.name}/system/api-system-probe",
            "System workflow task metadata should use the governor-scoped workflow id",
        )
        self.assert_event_contains(
            self.events_since(system_checkpoint),
            name="workflow_task_started",
            expected={
                "workflow_id": f"{vault.name}/system/api-system-probe",
                "task_id": system_task_id,
            },
        )
        self.soft_assert_equal(
            system_task.get("metadata", {}).get("workflow_result", {}).get("status"),
            "completed",
            "System workflow task metadata should include the terminal workflow result",
        )

        missing_active_chat_task = self.call_api(
            "/api/chat/sessions/not-active/active-task"
        )
        assert (
            missing_active_chat_task.status_code == 404
        ), "Missing active chat task returns 404"

        # Chat execution, metadata, history transforms
        chat_metadata = self.call_api("/api/metadata")
        assert chat_metadata.status_code == 200, "Metadata endpoint available"
        metadata_payload = chat_metadata.json()
        assert "default_model_thinking" in metadata_payload.get(
            "settings", {}
        ), "Metadata exposes default thinking hint for chat UI"
        assert (
            metadata_payload.get("settings", {}).get("default_chat_mode") == "normal"
        ), "Metadata exposes the default mode for new chat sessions"
        assert any(
            tool.get("name") == "workflow_run"
            for tool in metadata_payload.get("tools", [])
        ), "workflow_run tool is exposed in metadata"
        assert any(
            tool.get("name") == "session_ops"
            for tool in metadata_payload.get("tools", [])
        ), "session_ops tool is exposed in metadata"

        chat_payload = {
            "vault_name": vault.name,
            "prompt": "Say hello from integration test.",
            "tools": [],
            "model": "test",
            "thinking": "off",
        }
        chat_first = await self.run_chat_task(chat_payload)
        assert (
            chat_first["start_response"].status_code == 200
        ), "Chat task start succeeds"
        assert (
            chat_first["terminal_event"].get("event") == "done"
        ), "Chat task completes"
        session_id = chat_first["session_id"]

        from core.memory.session_summary import SessionSummaryStore

        summary_store = SessionSummaryStore(
            system_root=str(self._get_system_controller()._system_root)
        )
        summary_store.upsert_session_summary(
            vault_name=vault.name,
            session_id=session_id,
            summary="Original session summary.",
            domain="integration testing",
            work_product="api validation",
            user_intent="verify session summary preview",
            named_entities="IntegrationApiVault",
            source_summary="No external sources.",
            metadata={"source": "api_endpoint_validation"},
        )

        memory_preview = self.call_api(
            f"/api/chat/sessions/{session_id}/summary?vault_name={vault.name}"
        )
        assert (
            memory_preview.status_code == 200
        ), "Session summary preview endpoint succeeds"
        assert (
            memory_preview.json().get("summary") == "Original session summary."
        ), "Session summary preview returns summary"
        assert (
            memory_preview.json().get("vector_index", {}).get("expected_fields") == 4
        ), "Session summary preview exposes expected vector index coverage"

        memory_update = self.call_api(
            f"/api/chat/sessions/{session_id}/summary?vault_name={vault.name}",
            method="PUT",
            data={
                "summary": "Edited session summary.",
                "domain": "integration testing",
                "work_product": "manual session summary editing",
                "user_intent": "verify session summary editing",
                "named_entities": "IntegrationApiVault",
                "source_summary": "No external sources.",
                "metadata": {"source": "manual_api_edit"},
            },
        )
        assert (
            memory_update.status_code == 200
        ), "Session summary update endpoint succeeds"
        assert (
            memory_update.json().get("summary") == "Edited session summary."
        ), "Session summary update replaces summary"
        assert (
            memory_update.json().get("vector_index", {}).get("indexed_fields") == 4
        ), "Session summary update returns refreshed vector index coverage"

        title_update = self.call_api(
            f"/api/chat/sessions/{session_id}/title",
            method="PATCH",
            data={"vault_name": vault.name, "title": "Exported Session"},
        )
        assert title_update.status_code == 200, "Session title update succeeds"

        export_response = self.call_api(
            f"/api/chat/sessions/{session_id}/export",
            method="POST",
            data={"vault_name": vault.name},
        )
        assert export_response.status_code == 200, "Chat transcript export succeeds"
        export_path = Path(export_response.json()["path"])
        exported_markdown = export_path.read_text(encoding="utf-8")
        assert exported_markdown.startswith("---\n"), "Export includes frontmatter"
        assert (
            'title: "Exported Session"' in exported_markdown
        ), "Export frontmatter includes the user session title"
        assert (
            "session_summary: |-\n  Edited session summary." in exported_markdown
        ), "Export frontmatter includes the session summary"

        memory_delete = self.call_api(
            f"/api/chat/sessions/{session_id}/summary?vault_name={vault.name}",
            method="DELETE",
        )
        assert (
            memory_delete.status_code == 200
        ), "Session summary delete endpoint succeeds"
        assert (
            memory_delete.json().get("deleted") is True
        ), "Session summary delete reports deletion"

        chat_second = await self.run_chat_task(
            {
                **chat_payload,
                "session_id": session_id,
                "prompt": "Add another thought for history depth.",
            }
        )
        assert (
            chat_second["start_response"].status_code == 200
        ), "Follow-up chat task start succeeds"
        assert (
            chat_second["terminal_event"].get("event") == "done"
        ), "Follow-up chat task completes"

        chat_stream_result = await self.run_chat_task(
            {
                **chat_payload,
                "prompt": "Stream a short response for the integration test.",
            }
        )
        assert (
            chat_stream_result["start_response"].status_code == 200
        ), "Streaming chat task start succeeds"
        assert (
            chat_stream_result["terminal_event"].get("event") == "done"
        ), "Streaming chat task completes"
        chat_stream_task_id = chat_stream_result["task_id"]
        assert chat_stream_task_id, "Streaming chat task start returns a task id"
        chat_stream = self.call_api(f"/api/chat/tasks/{chat_stream_task_id}/events")
        assert chat_stream.status_code == 200, "Streaming chat event endpoint succeeds"
        assert (
            "data: " in chat_stream.text
        ), "Streaming chat returns SSE-formatted chunks"
        assert (
            '"event": "done"' in chat_stream.text
        ), "Streaming chat returns a terminal SSE event"
        assert (
            '"event": "error"' not in chat_stream.text
        ), "Streaming chat completes without an error event"

        chat_task_start = self.call_api(
            "/api/chat/tasks",
            method="POST",
            data={
                **chat_payload,
                "session_id": "api-task-stream-session",
                "prompt": "Start a task-owned stream for the integration test.",
            },
        )
        assert chat_task_start.status_code == 200, "Chat task start endpoint succeeds"
        chat_task_payload = chat_task_start.json()
        assert (
            chat_task_payload.get("session_id") == "api-task-stream-session"
        ), "Chat task start preserves the requested session id"
        chat_task_id = chat_task_payload.get("task", {}).get("task_id")
        assert chat_task_id, "Chat task start returns a task id"

        tool_limit_update = self.call_api(
            "/api/system/settings/general/chat_tool_calls_limit",
            method="PUT",
            data={"value": "1"},
        )
        assert (
            tool_limit_update.status_code == 200
        ), "Chat tool-call limit setting updates"

        from pydantic_ai.models.test import TestModel

        import core.chat.executor as chat_executor

        async def first_limited_tool() -> str:
            return "FIRST"

        async def second_limited_tool() -> str:
            return "SECOND"

        def _limited_tool_agent_config(
            vault_name,
            vault_path,
            tools,
            model,
            thinking=None,
            chat_mode=None,
        ):
            del vault_name, vault_path, tools, model, thinking, chat_mode
            return (
                "Use the available tools before answering.",
                "",
                TestModel(),
                [first_limited_tool, second_limited_tool],
            )

        original_prepare_agent_config = chat_executor._prepare_agent_config
        chat_executor._prepare_agent_config = _limited_tool_agent_config
        try:
            limited_chat = await self.run_chat_task(
                {
                    **chat_payload,
                    "prompt": "This run should hit the configured tool-call limit.",
                    "tools": ["first_limited_tool", "second_limited_tool"],
                }
            )
            limited_stream = await self.run_chat_task(
                {
                    **chat_payload,
                    "prompt": "This streaming run should hit the configured tool-call limit.",
                    "tools": ["first_limited_tool", "second_limited_tool"],
                }
            )
        finally:
            chat_executor._prepare_agent_config = original_prepare_agent_config
            reset_limit = self.call_api(
                "/api/system/settings/general/chat_tool_calls_limit",
                method="PUT",
                data={"value": "0"},
            )
            assert reset_limit.status_code == 200, "Chat tool-call limit setting resets"

        assert (
            limited_chat["start_response"].status_code == 200
        ), "Tool-call limited chat task should start"
        limited_payload = limited_chat["terminal_event"]
        assert (
            limited_payload.get("event") == "error"
        ), "Tool-call limit emits a terminal error event"
        assert (
            limited_payload.get("details", {}).get("setting") == "chat_tool_calls_limit"
        ), "Tool-call limit response identifies the controlling setting"
        assert (
            limited_stream["start_response"].status_code == 200
        ), "Streaming tool-call limited chat task should start"
        assert (
            limited_stream["terminal_event"].get("event") == "error"
        ), "Streaming tool-call limit emits error event"
        assert (
            "Tool-call limit reached" in limited_stream["text"]
        ), "Streaming tool-call limit returns actionable error text"

        image_path = vault / "tiny.png"
        image_path.write_bytes(
            base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO2p6b8AAAAASUVORK5CYII="
            )
        )
        capability_mismatch = await self.run_chat_task(
            {
                "vault_name": vault.name,
                "prompt": "Describe this image.",
                "tools": [],
                "model": "test",
                "image_paths": [str(image_path)],
            },
        )
        assert (
            capability_mismatch["start_response"].status_code == 200
        ), "Vision mismatch chat task should start"
        mismatch_payload = capability_mismatch["terminal_event"]
        assert (
            mismatch_payload.get("event") == "error"
        ), "Capability mismatch emits an error event"
        assert (
            "vision" in capability_mismatch["text"].lower()
        ), "Capability mismatch guidance mentions vision"

        await self.stop_system()
        self.teardown_scenario()


# === WORKFLOW TEMPLATES ===

STATUS_PROBE_WORKFLOW = """---
run_type: workflow
enabled: false
description: Validation helper workflow
---

## Run

```python
await finish(status="completed", reason="status-probe")
```
"""


SKIPPED_PROBE_WORKFLOW = """---
run_type: workflow
enabled: false
description: Validation skipped workflow
---

## Run

```python
await finish(status="skipped", reason="status-probe-skipped")
```
"""


API_SYSTEM_PROBE_WORKFLOW = """---
run_type: workflow
enabled: false
description: API system workflow routing probe
---

## Run

```python
await finish(status="completed", reason="api-system-probe")
```
"""


if __name__ == "__main__":
    asyncio.run(ApiEndpointsScenario().test_scenario())
