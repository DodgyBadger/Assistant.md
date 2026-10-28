"""Monty authoring runs real delegate tools and writes their output artifacts."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from validation.pytest_scenarios.application import Application
from validation.pytest_scenarios.support.delegate_workflow_inputs import (
    DELEGATE_MARKDOWN_IMAGE_WORKFLOW,
    DELEGATE_WITH_TOOLS_WORKFLOW,
)
from validation.pytest_scenarios.support.vault_recovery import run_workflow

pytestmark = pytest.mark.scenario


@pytest.mark.parametrize(
    "name, workflow, output",
    [
        (
            "delegate_with_tools",
            DELEGATE_WITH_TOOLS_WORKFLOW,
            "delegate-with-tools-result.md",
        ),
        (
            "delegate_markdown_image",
            DELEGATE_MARKDOWN_IMAGE_WORKFLOW,
            "delegate-markdown-image-result.md",
        ),
    ],
)
async def test_delegate_authoring_outputs_and_binding_events(
    app: Application, name, workflow, output
):
    vault = app.vault("DelegateToolVault")
    app.write(vault, "notes/content.md", "DELEGATE_CONTENT")
    app.write(
        vault,
        "notes/with-image.md",
        "Review this embedded image.\n\n![Example](../images/test_image.jpg)\n",
    )
    (vault / "images").mkdir()
    shutil.copyfile(
        Path("validation/fixtures/files/test_image.jpg"),
        vault / "images/test_image.jpg",
    )
    app.write(vault, f"AssistantMD/Authoring/{name}.md", workflow)
    await app.start()
    checkpoint = app.events.checkpoint()
    result = await run_workflow(app, vault, name, expect_failure=False)
    assert result.status == "completed"
    artifact = vault / "outputs" / output
    assert artifact.exists()
    if name == "delegate_with_tools":
        assert artifact.read_text().strip()
        app.events.require(
            "authoring_direct_tool_started",
            after=checkpoint,
            workflow_id=f"{vault.name}/{name}",
            tool="delegate",
        )
        app.events.require(
            "authoring_direct_tool_completed",
            after=checkpoint,
            workflow_id=f"{vault.name}/{name}",
            tool="delegate",
        )
        app.events.require(
            "delegate_completed", after=checkpoint, workflow_id=f"{vault.name}/{name}"
        )
    app.events.require(
        "delegate_tool_binding_resolved",
        after=checkpoint,
        workflow_id=f"{vault.name}/{name}",
        requested=["file_read"],
    )
