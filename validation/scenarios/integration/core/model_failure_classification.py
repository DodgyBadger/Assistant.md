"""Exercise model failure classification through a local provider boundary."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

from validation.core.base_scenario import BaseScenario


class _RejectingProvider(BaseHTTPRequestHandler):
    requests: list[tuple[str, str]] = []

    def do_POST(self) -> None:
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.requests.append((self.path, self.headers.get("Authorization", "")))
        body = json.dumps(
            {
                "error": {
                    "type": "invalid_request_error",
                    "code": "billing_error",
                    "message": (
                        'Your credit balance is too low. api_key="validation-provider-key" '
                        "refresh_token=PRIVATE_REFRESH Bearer PRIVATE_BEARER "
                        "data:application/pdf;base64,PRIVATE_DOCUMENT "
                        "https://user:password@example.test/path?token=PRIVATE_QUERY\n"
                        + "x" * 2000
                    ),
                    "input": "PRIVATE_PROVIDER_INPUT",
                }
            }
        ).encode()
        self.send_response(400)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        pass


class ModelFailureClassificationScenario(BaseScenario):
    """Validate model provider failures become structured recovery metadata."""

    async def test_scenario(self) -> None:
        vault = self.create_vault("ModelFailureClassificationVault")
        _RejectingProvider.requests = []
        server = ThreadingHTTPServer(("127.0.0.1", 0), _RejectingProvider)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            await self.start_system()
            for name, value in (
                (
                    "VALIDATION_PROVIDER_URL",
                    f"http://127.0.0.1:{server.server_port}/v1",
                ),
                ("VALIDATION_PROVIDER_KEY", "validation-provider-key"),
            ):
                secret = self.call_api(
                    "/api/system/secrets",
                    method="PUT",
                    data={"name": name, "value": value},
                )
                assert secret.status_code == 200
            provider = self.call_api(
                "/api/system/providers/validation-provider",
                method="PUT",
                data={
                    "base_url": "VALIDATION_PROVIDER_URL",
                    "api_key": "VALIDATION_PROVIDER_KEY",
                },
            )
            assert provider.status_code == 200, provider.text
            model = self.call_api(
                "/api/system/models/validation-provider-model",
                method="PUT",
                data={
                    "provider": "validation-provider",
                    "model_string": "local-model",
                    "capabilities": ["text"],
                },
            )
            assert model.status_code == 200, model.text
            session_id = "model_failure_classification_session"
            result = await self.run_chat_task(
                {
                    "vault_name": vault.name,
                    "prompt": "Trigger provider billing failure.",
                    "session_id": session_id,
                    "tools": [],
                    "model": "validation-provider-model",
                }
            )
            assert result["start_response"].status_code == 200
            error_event = result["terminal_event"]
            assert error_event.get("event") == "error"
            assert error_event.get("details", {}).get("failure_kind") == "billing"
            assert error_event.get("details", {}).get("retryable") is False
            assert error_event.get("details", {}).get("http_status") == 400
            assert _RejectingProvider.requests
            assert all(
                authorization == "Bearer validation-provider-key"
                for _, authorization in _RejectingProvider.requests
            )

            detail = self.call_api(
                f"/api/chat/sessions/{session_id}?vault_name={vault.name}"
            )
            assert detail.status_code == 200
            latest_failure = detail.json().get("latest_failure")
            assert latest_failure is not None
            assert latest_failure.get("error_type") == "ModelHTTPError"
            assert latest_failure.get("failure_kind") == "billing"
            assert latest_failure.get("retryable") is False
            assert latest_failure.get("http_status") == 400
            assert latest_failure.get("suggested_action")
            assert "credit balance" in latest_failure["error"]

            activity_log = self.call_api("/api/system/activity-log")
            assert activity_log.status_code == 200
            entries = activity_log.json()["entries"]
            assert any(
                entry["data"].get("failure_kind") == "billing"
                and entry["data"].get("retryable") is False
                for entry in entries
            )
            content = json.dumps(entries)
            diagnostics = [
                entry["data"]
                for entry in entries
                if entry["data"].get("event") == "chat_turn_failed"
                and entry["data"].get("session_id") == session_id
            ]
            assert diagnostics
            assert diagnostics[0]["provider_error_type"] == "invalid_request_error"
            assert diagnostics[0]["provider_error_code"] == "billing_error"
            assert len(diagnostics[0]["provider_error_message"]) <= 500
            for artifact in (json.dumps(error_event), detail.text, content):
                for private in (
                    "PRIVATE_PROVIDER_INPUT",
                    "validation-provider-key",
                    "PRIVATE_REFRESH",
                    "PRIVATE_BEARER",
                    "PRIVATE_DOCUMENT",
                    "user:password",
                    "PRIVATE_QUERY",
                ):
                    assert private not in artifact
        finally:
            try:
                await self.stop_system()
                self.teardown_scenario()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)
