"""Real authoring boundaries with controlled history and configured test-model inputs."""

from __future__ import annotations

import pytest

from validation.pytest_scenarios.application import Application
from validation.pytest_scenarios.support.authoring import (
    contract_vault,
    run_contract_workflow,
)

pytestmark = pytest.mark.scenario


async def test_authoring_helpers_with_configured_test_model(app: Application) -> None:
    vault = contract_vault(app)
    checkpoint = app.events.checkpoint()
    await app.start()
    app.events.require(
        "workflow_loaded",
        after=checkpoint,
        workflow_id="AuthoringContractVault/authoring_contract_success",
    )
    checkpoint = app.events.checkpoint()
    result = await run_contract_workflow(app, vault)
    assert result["status"] == "completed", "Contract success workflow should complete"
    app.events.require(
        "authoring_monty_execution_started",
        after=checkpoint,
        workflow_id="AuthoringContractVault/authoring_contract_success",
    )
    app.events.require(
        "authoring_direct_tool_started",
        after=checkpoint,
        workflow_id="AuthoringContractVault/authoring_contract_success",
        tool="file_read",
    )
    app.events.require(
        "authoring_direct_tool_completed",
        after=checkpoint,
        workflow_id="AuthoringContractVault/authoring_contract_success",
        tool="file_read",
    )
    app.events.require(
        "authoring_direct_tool_started",
        after=checkpoint,
        workflow_id="AuthoringContractVault/authoring_contract_success",
        tool="delegate",
    )
    app.events.require(
        "authoring_direct_tool_completed",
        after=checkpoint,
        workflow_id="AuthoringContractVault/authoring_contract_success",
        tool="delegate",
    )
    app.events.require(
        "authoring_retrieve_history_completed",
        after=checkpoint,
        workflow_id="AuthoringContractVault/authoring_contract_success",
        item_count=0,
    )
    app.events.require(
        "authoring_assemble_context_completed",
        after=checkpoint,
        workflow_id="AuthoringContractVault/authoring_contract_success",
        message_count=3,
        instruction_count=1,
    )
    app.events.require(
        "authoring_parse_markdown_started",
        after=checkpoint,
        workflow_id="AuthoringContractVault/authoring_contract_success",
    )
    app.events.require(
        "authoring_parse_markdown_completed",
        after=checkpoint,
        workflow_id="AuthoringContractVault/authoring_contract_success",
        heading_count=2,
        section_count=2,
        code_block_count=1,
        image_count=1,
    )
    app.events.require(
        "authoring_pending_files_filtered",
        after=checkpoint,
        workflow_id="AuthoringContractVault/authoring_contract_success",
        pending_count=1,
    )
    app.events.require(
        "authoring_pending_files_completed",
        after=checkpoint,
        workflow_id="AuthoringContractVault/authoring_contract_success",
        completed_count=1,
    )
    app.events.require(
        "authoring_finish_requested",
        after=checkpoint,
        workflow_id="AuthoringContractVault/authoring_contract_success",
        status="completed",
        reason="all-helpers-exercised",
    )
    app.events.require(
        "authoring_monty_execution_completed",
        after=checkpoint,
        workflow_id="AuthoringContractVault/authoring_contract_success",
        status="completed",
        reason="all-helpers-exercised",
    )
    contract_output = vault / "outputs" / "contract-success.md"
    assert contract_output.exists(), "Expected contract success output file"
    if contract_output.exists():
        content = contract_output.read_text(encoding="utf-8")
        assert "SEED_CONTENT" in content, "Expected retrieved file content in output"
        assert (
            "RETURN_VALUE_CLEAN=True" in content
        ), "Expected clean file return_value contract"
        assert (
            "CONTENT_NONE=True" in content
        ), "Expected file read content side-channel to be empty"
        assert "ITEMS=1" in content, "Expected file read item projection"
        assert (
            "IMAGE_RETURN_LIST=True" in content
        ), "Expected image read multimodal return_value"
        assert (
            "IMAGE_CONTENT_NONE=True" in content
        ), "Expected image read content side-channel to be empty"
        assert (
            "MARKDOWN_IMAGE_RETURN_LIST=True" in content
        ), "Expected markdown image interleaved return_value"
        assert (
            "MARKDOWN_IMAGE_CONTENT_NONE=True" in content
        ), "Expected markdown image content side-channel to be empty"
        assert "ASSEMBLED=3" in content, "Expected assembled context count in output"
        assert (
            "HISTORY_ITEMS=0" in content
        ), "Expected retrieve_history output in contract file"
    delegate_output = vault / "outputs" / "delegate-success.md"
    assert delegate_output.exists(), "Expected delegate output file"
    if delegate_output.exists():
        assert (
            delegate_output.stat().st_size > 0
        ), "Expected delegate output file to be non-empty"
    parsed_output = vault / "outputs" / "parse-markdown.md"
    assert parsed_output.exists(), "Expected markdown parse output file"
    if parsed_output.exists():
        parsed_content = parsed_output.read_text(encoding="utf-8")
        assert (
            "Skill Example" in parsed_content
        ), "Expected parsed frontmatter name in output"
        assert (
            "AI In Fiction" in parsed_content
        ), "Expected parsed section heading in output"
        assert (
            "python" in parsed_content
        ), "Expected parsed code block language in output"
        assert (
            "../images/test_image.jpg" in parsed_content
        ), "Expected parsed image ref in output"
