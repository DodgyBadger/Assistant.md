"""Historical edit proposals remain readable while all former action endpoints reject."""

from __future__ import annotations

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.historical_proposals import (
    seed_historical_proposal,
)

pytestmark = pytest.mark.scenario


async def test_historical_edit_proposal_is_readable_and_cannot_mutate_files(
    app: Application,
) -> None:
    vault = app.vault("EditProposalArtifactsVault")
    app.write(vault, "README.md", "Before")
    await app.start()
    session_id, artifact_ref = seed_historical_proposal(app, vault.name)
    endpoint = (
        f"/api/vaults/{vault.name}/chat/{session_id}/edit-proposals/{artifact_ref}"
    )
    response = app.request(endpoint)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["artifact_ref"] == artifact_ref
    assert payload["status"] == "pending"
    assert payload["edits"][0]["replacement_text"] == "After"
    for action in ("apply", "deny", "review"):
        rejected = app.request(f"{endpoint}/{action}", method="POST", data={})
        assert rejected.status_code in {404, 405}, rejected.text
    assert (vault / "README.md").read_text(encoding="utf-8") == "Before"
