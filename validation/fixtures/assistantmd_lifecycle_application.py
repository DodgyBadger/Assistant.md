"""Exercise real AssistantMD resources through the validation adapter."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from threading import Thread

from validation.adapters.assistantmd import AssistantMDAdapter
from validation.framework.lifecycle import RunContext, execute


async def run(root: Path, mode: str) -> None:
    from core.runtime.paths import snapshot_bootstrap_roots
    from core.runtime.state import has_runtime_context

    if mode == "cache_restore":
        from core.runtime.paths import set_bootstrap_roots

        caller_data, caller_system = root / "caller/data", root / "caller/system"
        caller_system.mkdir(parents=True)
        caller_data.mkdir(parents=True)
        set_bootstrap_roots(caller_data, caller_system)
        from validation.core.configuration import seed_configuration

        seed_configuration(caller_system)
        from core.llm.model_utils import refresh_model_cache
        from core.settings.store import ModelConfig, load_settings, save_settings

        existing = load_settings()
        existing.models["caller-only"] = ModelConfig(
            provider="openai", model_string="caller-model"
        )
        save_settings(existing)
        refresh_model_cache()
        from core.logger import UnifiedLogger

        UnifiedLogger(tag="caller", default_sinks=["activity"]).info(
            "caller_log_remains_owned"
        )

    caller_streams = [
        handler.stream
        for handler in logging.getLogger("project_assistant.activity").handlers
        if isinstance(handler, logging.FileHandler) and handler.stream is not None
    ]
    before_roots = snapshot_bootstrap_roots()
    before_environment = {
        name: os.environ.get(name)
        for name in ("ASSISTANTMD_SECRETS_KEY", "LOGFIRE_TOKEN")
    }

    class StartupCancellationContext(RunContext):
        injected = False

        def emit(self, event: str, **data: object) -> None:
            super().emit(event, **data)
            record = data.get("record")
            if (
                mode == "startup_cancel"
                and not self.injected
                and event == "application_event"
                and isinstance(record, dict)
                and record.get("data", {}).get("event")
                == "workflow_scheduler_sync_completed"
            ):
                self.injected = True
                raise asyncio.CancelledError("cancel at published startup state")

    context = StartupCancellationContext(
        root, secrets={"api_fixture": "fixture-private-api-credential"}
    )
    adapter = AssistantMDAdapter()
    client = None
    original_clock = None
    foreign_logger = logging.getLogger("project_assistant.activity")

    class BorrowedHandler(logging.Handler):
        closed_by_adapter = False

        def emit(self, record: logging.LogRecord) -> None:
            pass

        def close(self) -> None:
            self.closed_by_adapter = True
            super().close()

    borrowed = BorrowedHandler()
    foreign_logger.addHandler(borrowed)
    owned_streams = []

    async def body() -> None:
        nonlocal client, original_clock
        import core.authoring.runtime.host as host
        from core.logger import UnifiedLogger
        from validation.core.base_scenario import BaseScenario

        original_clock = host.datetime
        logger = UnifiedLogger(
            tag="adapter-fixture", default_sinks=["validation", "activity"]
        )
        logger.info(
            "fixture_before_start",
            data={
                "event": "fixture_before_start",
                "credential": os.environ["ASSISTANTMD_SECRETS_KEY"],
            },
        )

        if mode == "thread_logging":
            from core.runtime.paths import set_bootstrap_roots

            alternate = root / "alternate-system"
            set_bootstrap_roots(root / "alternate-data", alternate)
            worker = Thread(
                target=lambda: logger.info(
                    "fixture_plain_thread",
                    data={
                        "event": "fixture_plain_thread",
                        "credential": context.secret("api_fixture"),
                    },
                )
            )
            worker.start()
            worker.join(timeout=10)
            assert not worker.is_alive()
            owned_streams.extend(
                handler.stream
                for handler in foreign_logger.handlers
                if isinstance(handler, logging.FileHandler)
                and Path(handler.baseFilename).parent == alternate
                and handler.stream is not None
            )

        if mode == "cache_restore":
            from core.llm.model_utils import resolve_model
            from core.settings.model_registry import UnknownModelAlias

            try:
                resolve_model("caller-only")
            except UnknownModelAlias:
                pass
            else:
                raise AssertionError("Caller model cache leaked into the scenario")

        if mode == "restart":
            for index in range(130):
                logger.info(
                    "fixture_seed", data={"event": "fixture_seed", "index": index}
                )

        class FixtureScenario(BaseScenario):
            async def test_scenario(self) -> None:
                self.create_vault("LifecycleVault")
                if mode == "prepare_failure":
                    database = self.run_path / "system/settings.db"
                    database.parent.mkdir(parents=True, exist_ok=True)
                    database.mkdir()
                controller = self._get_system_controller()
                nonlocal client
                client = controller.get_api_test_client()
                controller.set_test_date(datetime(2023, 3, 1))
                if mode == "prepared_failure":
                    raise AssertionError("failure before runtime startup")
                if mode == "invalid_configuration":
                    configuration = self.configuration_snapshot()
                    configuration["settings"]["default_model"]["value"] = None
                    self.install_configuration_fixture(configuration)
                if mode == "startup_failure":
                    data = self.run_path / "test_vaults"
                    shutil.rmtree(data)
                    data.write_text("invalid data root")
                await self.start_system()
                assert has_runtime_context()
                if mode == "execution_failure":
                    raise AssertionError("failure while runtime is active")
                assert self.call_api("/api/status").status_code == 200
                if mode == "restart":
                    checkpoint = self.event_checkpoint()
                    await self.restart_system()
                    logger.info(
                        "fixture_after_restart", data={"event": "fixture_after_restart"}
                    )
                    events = self.events_since(checkpoint)
                    self.assert_event_contains(
                        events, name="fixture_after_restart", expected={}
                    )
                    assert not any(
                        event.get("name") == "fixture_seed" for event in events
                    )
                credential_input = {
                    "name": "FIXTURE_API_CREDENTIAL",
                    "value": context.secret("api_fixture"),
                }
                response = self.call_api(
                    "/api/system/secrets", method="PUT", data=credential_input
                )
                assert response.status_code == 200
                from core.identity import LOCAL_USER_AUTHORITY, use_execution_authority
                from core.settings.secrets_store import get_secret_value

                with use_execution_authority(LOCAL_USER_AUTHORITY):
                    assert get_secret_value("FIXTURE_API_CREDENTIAL") == context.secret(
                        "api_fixture"
                    )
                assert credential_input["value"] == context.secret("api_fixture")
                (root / "application-output.md").write_text(
                    "# Real application completed\n"
                )
                # The framework must stop this application; there is no manual stop.

        scenario = FixtureScenario()
        await scenario.test_scenario()

    if mode == "adapter_setup_failure":
        (root / "bootstrap").write_text("blocks bootstrap directory acquisition")
    before_tasks = set(asyncio.all_tasks())
    outcome = await execute(context, adapter, body)
    await asyncio.sleep(0)
    remaining_tasks = [
        task
        for task in asyncio.all_tasks()
        if task not in before_tasks and not task.done()
    ]
    host = sys.modules.get("core.authoring.runtime.host")

    model_cache_restored = True
    if mode == "cache_restore":
        from core.llm.model_utils import resolve_model

        model_cache_restored = resolve_model("caller-only") == (
            "openai",
            "caller-model",
        )
    report = {
        "caller_streams_preserved": all(not stream.closed for stream in caller_streams),
        "caller_stream_count": len(caller_streams),
        "borrowed_handler_preserved": borrowed in foreign_logger.handlers
        and not borrowed.closed_by_adapter,
        "owned_streams_closed": all(stream.closed for stream in owned_streams),
        "model_cache_restored": model_cache_restored,
        "passed": outcome.passed,
        "primary_phase": outcome.primary_phase,
        "cancelled": outcome.cancelled,
        "startup_cancel_injected": context.injected,
        "primary_type": (
            type(outcome.primary_error).__name__ if outcome.primary_error else None
        ),
        "cleanup_errors": len(outcome.cleanup_errors),
        "evidence_errors": len(outcome.evidence_errors),
        "roots_restored": before_roots == snapshot_bootstrap_roots(),
        "environment_restored": before_environment
        == {name: os.environ.get(name) for name in before_environment},
        "runtime_released": not has_runtime_context(),
        "background_tasks_released": not remaining_tasks,
        "clock_restored": original_clock is None
        or (host is not None and host.datetime is original_clock),
        "client_closed": client is None or client.is_closed,
    }
    (root / "adapter-result.json").write_text(json.dumps(report, indent=2))
    foreign_logger.removeHandler(borrowed)
    borrowed.close()


if __name__ == "__main__":
    asyncio.run(run(Path(sys.argv[1]), sys.argv[2]))
