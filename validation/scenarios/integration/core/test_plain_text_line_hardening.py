"""Real file/review/attachment boundaries exposed by branch hardening review."""

from __future__ import annotations

import pytest

from validation.support.application import Application
from validation.support.vault.file_tools import BoundFileTools

pytestmark = pytest.mark.scenario


@pytest.mark.parametrize("separator", ["\f", "\u2028"])
async def test_line_edits_count_physical_lines_in_plain_text(
    app: Application, separator: str
) -> None:
    vault = app.vault("PhysicalTextLineVault")
    content = f"alpha{separator}bravo\r\ncharlie\r\n"
    app.write(vault, "sample.txt", content)
    await app.start()
    result = await BoundFileTools(app, vault).call(
        "file_write",
        operation="edit_line",
        path="sample.txt",
        line_number=2,
        old_text="charlie",
        new_text="changed",
    )
    assert result.metadata["status"] == "completed", result.return_value
    assert (
        vault / "sample.txt"
    ).read_bytes() == f"alpha{separator}bravo\r\nchanged\r\n".encode()
