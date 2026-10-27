"""
API client for V2 validation scenarios.

Provides high-level API testing capabilities backed by a FastAPI TestClient
supplied by the system controller so scenarios exercise the same app instance
that the validation runtime bootstraps.
"""

import json
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from scenario_harness.lifecycle import current_run_context

from core.logger import UnifiedLogger


@dataclass
class APIResponse:
    """Wrapper for API response data."""

    status_code: int
    data: dict | None = None
    text: str = ""

    def json(self) -> dict:
        return self.data or {}


_CREDENTIAL_FIELDS = frozenset({"access_token", "refresh_token", "client_secret"})


def _register_credentials(value: Any) -> None:
    """Register explicitly named credentials without treating ordinary values as secrets."""
    context = current_run_context()
    if context is None:
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key in _CREDENTIAL_FIELDS and isinstance(item, str):
                context.register_secret(item)
            _register_credentials(item)
    elif isinstance(value, list | tuple):
        for item in value:
            _register_credentials(item)


def _register_request_credentials(
    endpoint: str, data: dict | None, params: dict | None
) -> None:
    context = current_run_context()
    if context is None:
        return
    _register_credentials(data)
    _register_credentials(params)
    path = endpoint.split("?", 1)[0].rstrip("/")
    key = (
        "value"
        if path == "/api/system/secrets"
        else (
            "owner_token"
            if path == "/auth/session"
            else "code" if "/oauth/complete" in path else None
        )
    )
    if key is not None:
        for source in (data, params):
            if source is not None and isinstance(source.get(key), str):
                context.register_secret(source[key])


class APIClient:
    """Client for AssistantMD API and CLI testing."""

    def __init__(self, test_data_root: Path, client: TestClient):
        self.logger = UnifiedLogger(tag="api-client")
        self.test_data_root = Path(test_data_root)
        self.test_data_root.mkdir(parents=True, exist_ok=True)
        self._client = client

        # Track interactions for artifact logging
        self.api_calls: list[dict[str, Any]] = []

    # === PUBLIC API ===

    def call_api(
        self,
        endpoint: str,
        method: str = "GET",
        data: dict | None = None,
        params: dict | None = None,
        headers: dict | None = None,
    ) -> APIResponse:
        """Call AssistantMD API endpoint through FastAPI TestClient."""
        method = method.upper()
        _register_request_credentials(endpoint, data, params)
        interaction = {
            "timestamp": datetime.now().isoformat(),
            "method": method,
            "endpoint": endpoint,
            "data": data,
            "params": params,
        }

        response = None
        try:
            response = self._client.request(
                method=method,
                url=endpoint,
                json=data,
                params=params,
                headers=headers,
            )
        finally:
            # Even if request blows up we record the attempt
            self.api_calls.append(interaction)

        payload: dict | None = None
        text = ""

        if response is not None:
            text = response.text
            try:
                payload = response.json()
            except ValueError:
                payload = None

            _register_credentials(payload)
            interaction["status_code"] = response.status_code
            if payload is not None:
                interaction["response"] = payload
            else:
                interaction["response_text"] = text

        return APIResponse(
            status_code=response.status_code if response else 0,
            data=payload,
            text=text,
        )

    def save_interaction_log(
        self,
        log_file: Path,
        projector: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ) -> None:
        """Save all API and CLI interactions to single log file."""
        with open(log_file, "w") as handle:
            handle.write("# System Interactions Log\n\n")

            if self.api_calls:
                handle.write("## API Calls\n\n")
                for original_call in self.api_calls:
                    call = deepcopy(original_call)
                    if projector is not None:
                        call = projector(call)
                    timestamp = call["timestamp"]
                    method = call["method"]
                    endpoint = call["endpoint"]
                    status = call.get("status_code", "n/a")
                    handle.write(f"**{timestamp}**: {method} {endpoint} → {status}\n")
                    if call.get("params"):
                        handle.write(
                            f"  Params: {json.dumps(call['params'], indent=2)}\n"
                        )
                    if call.get("data"):
                        handle.write(
                            f"  Payload: {json.dumps(call['data'], indent=2)}\n"
                        )
                    if call.get("response"):
                        handle.write(
                            f"  Response: {json.dumps(call['response'], indent=2)}\n"
                        )
                    elif call.get("response_text"):
                        handle.write(f"  Response Text: {call['response_text']}\n")
                    handle.write("\n")

    def shutdown(self):
        """No-op for compatibility; lifetime managed by the system controller."""
        return None
