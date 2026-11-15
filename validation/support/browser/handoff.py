"""Real handoff endpoint paths and bounded readiness observation."""

from __future__ import annotations

import asyncio

from validation.support.application import Application


def handoff_path(session_id: str) -> str:
    return f"/api/chat/sessions/{session_id}/browser-handoff"


async def waiting_handoff(app: Application, operation: asyncio.Task, session_id: str):
    async with asyncio.timeout(6):
        while True:
            response = await app.request_async(handoff_path(session_id))
            if response.status_code == 200:
                assert (
                    not operation.done()
                ), "Handoff returned before the human handback"
                return response.json()
            assert (
                not operation.done()
            ), "Handoff must wait and advertise its owning task"
            await asyncio.sleep(0.05)
