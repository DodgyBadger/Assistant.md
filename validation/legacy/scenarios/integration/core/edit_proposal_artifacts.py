"""Integration scenario for read-only historical edit proposals."""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from core.chat.chat_store import ChatStore
from core.chat.schema import DB_NAME
from core.database import connect_sqlite_from_system_db
from validation.support.base_scenario import BaseScenario


class EditProposalArtifactsScenario(BaseScenario):
    """Verify legacy proposal rows remain readable but cannot execute."""

    async def test_scenario(self):
        vault = self.create_vault("EditProposalArtifactsVault")
        await self.start_system()

        session_id = "edit_proposal_artifacts_session"
        artifact_ref = "edit-proposals/historical-artifact"
        store = ChatStore(system_root=str(self._get_system_controller()._system_root))
        store.ensure_session(
            session_id,
            vault.name,
            owner_principal_id="local-user",
        )
        proposal = {
            "artifact_ref": artifact_ref,
            "artifact_kind": "file_edit_proposal",
            "vault_name": vault.name,
            "session_id": session_id,
            "title": "Historical proposal",
            "summary": "Retained for chat rendering.",
            "status": "pending",
            "edits": [
                {
                    "edit_id": "historical-edit",
                    "operation": "replace_text",
                    "path": "README.md",
                    "original_text": "Before",
                    "replacement_text": "After",
                }
            ],
        }
        conn = connect_sqlite_from_system_db(DB_NAME)
        try:
            conn.execute(
                """
                INSERT INTO chat_edit_proposals (
                    artifact_ref, session_id, vault_name, status, proposal_json
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    artifact_ref,
                    session_id,
                    vault.name,
                    "pending",
                    json.dumps(proposal),
                ),
            )
            conn.commit()
        finally:
            conn.close()

        endpoint = (
            f"/api/vaults/{vault.name}/chat/{session_id}/edit-proposals/{artifact_ref}"
        )
        fetched = self.call_api(endpoint)
        assert fetched.status_code == 200, "Historical proposal should remain fetchable"
        payload = fetched.json()
        assert payload["artifact_ref"] == artifact_ref
        assert payload["status"] == "pending"
        assert payload["edits"][0]["replacement_text"] == "After"

        for action in ("apply", "deny", "review"):
            response = self.call_api(f"{endpoint}/{action}", method="POST", data={})
            assert response.status_code in {
                404,
                405,
            }, f"Historical proposal action {action} must not be executable"


if __name__ == "__main__":
    asyncio.run(EditProposalArtifactsScenario().run())
