"""Retire summary surfaces and packaged workflow without rewriting authored files."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from core.authoring.contracts import UnknownAuthoringCapabilityError
from core.authoring.helpers import get_builtin_helper_definitions
from core.authoring.registry import AuthoringCapabilityRegistry
from core.authoring.template_discovery import seed_system_templates
from core.identity import LOCAL_USER_AUTHORITY
from core.runtime.execution_tasks import ExecutionTaskSource
from core.runtime.state import get_runtime_context
from validation.core.base_scenario import BaseScenario, with_local_user_authority


class SessionDiscoveryCutoverScenario(BaseScenario):
    @with_local_user_authority
    async def test_scenario(self):
        vault = self.create_vault("DiscoveryCutoverVault")
        system_root = self._get_system_controller()._system_root
        source = system_root / "Authoring" / "nightly-session-summarization.md"
        source.parent.mkdir(parents=True, exist_ok=True)
        fixture = Path("validation/fixtures/retired/nightly-session-summarization.md")
        packaged = fixture.read_text(encoding="utf-8").replace(
            "enabled: false", "enabled: true", 1
        )
        source.write_text(packaged, encoding="utf-8")
        self.create_file(
            vault,
            "AssistantMD/Authoring/retired_summary_operation.md",
            RETIRED_OPERATION_WORKFLOW,
        )
        await self.start_system()
        try:
            assert not source.exists()
            backups = list((system_root / "backups" / "retired_workflows").glob("*.md"))
            assert len(backups) == 1
            assert backups[0].read_text(encoding="utf-8") == packaged
            result = seed_system_templates(system_root)
            assert result["retired"] == [] and not source.exists()
            assert len(list(backups[0].parent.glob("*.md"))) == 1

            # A modified copy is authored state, not an owned package to overwrite.
            customized = (
                packaged + "enabled: Authored body content remains untouched.\n"
            )
            source.write_text(customized, encoding="utf-8")
            result = seed_system_templates(system_root, overwrite=True)
            assert result["retired"] == []
            assert source.read_text(encoding="utf-8") == customized

            registry = AuthoringCapabilityRegistry(get_builtin_helper_definitions())
            assert "retrieve_sessions" not in registry.list_names()
            try:
                registry.resolve("retrieve_sessions")
            except UnknownAuthoringCapabilityError as exc:
                assert "retired" in str(exc) and "session_ops" in str(exc)
            else:
                raise AssertionError("Retired helper must fail explicitly")

            runtime = get_runtime_context()
            try:
                await runtime.workflow_governor.execute_workflow(
                    global_id=f"{vault.name}/retired_summary_operation",
                    source=ExecutionTaskSource.API,
                    authority=LOCAL_USER_AUTHORITY,
                )
            except Exception:
                pass
            else:
                raise AssertionError("Retired summary operation must not succeed")
            run = runtime.workflow_run_store.get_latest_run(
                f"{vault.name}/retired_summary_operation"
            )
            assert run is not None and run.status == "failed"
            assert "retired" in str(run.reason) and "search_sessions" in str(run.reason)

            runtime.chat_store.ensure_session(
                "short", vault.name, owner_principal_id="local-user"
            )
            listed = self.call_api(f"/api/chat/sessions?vault_name={vault.name}")
            assert listed.status_code == 200
            assert "has_summary" not in listed.text
            for method in ("GET", "PUT", "DELETE"):
                response = self.call_api(
                    f"/api/chat/sessions/short/summary?vault_name={vault.name}",
                    method=method,
                    data={} if method == "PUT" else None,
                )
                assert response.status_code == 404
        finally:
            await self.stop_system()
            self.teardown_scenario()


RETIRED_OPERATION_WORKFLOW = """---
run_type: workflow
enabled: false
description: Validate explicit rejection of a retired summary operation
---

```python
await session_ops(operation="summarize_session")
```
"""
