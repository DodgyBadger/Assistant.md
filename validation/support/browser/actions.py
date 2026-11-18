"""Native browser form inputs and observation helpers shared by scenarios."""

from __future__ import annotations

import json

from pydantic_ai.messages import ToolReturn

from validation.support.application import Application

FORM = b"""<form method="post" action="/submit">
<label>Name<input name="name"></label>
<label>Colour<select name="colour"><option value="green">Green</option><option value="blue">Blue</option></select></label>
<button id="save">Save</button></form>"""


def element(view: dict, label: str) -> str:
    controls = view.get("elements")
    assert (
        isinstance(controls, list) and controls
    ), "A real page observation must expose actionable controls"
    matches = [item["ref"] for item in controls if item["label"] == label]
    assert len(matches) == 1, (label, controls)
    return matches[0]


def view(result: str | ToolReturn) -> dict:
    assert isinstance(result, str), "Browser action must return a new page observation"
    return json.loads(result)


async def configure(app: Application) -> None:
    await app.start()
    for key, value in (
        ("browser_min_memory_headroom_mb", "0"),
        ("browser_max_concurrent_sessions", "2"),
    ):
        assert (
            app.request(
                f"/api/system/settings/general/{key}", "PUT", {"value": value}
            ).status_code
            == 200
        )
