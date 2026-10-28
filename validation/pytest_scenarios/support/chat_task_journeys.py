"""Chat task API actions, bounded polling and SSE decoding."""

import asyncio
import json

from validation.pytest_scenarios.application import Application


async def start_chat(app: Application, vault: str, session: str, prompt: str) -> str:
    response = await app.request_async(
        "/api/chat/tasks",
        method="POST",
        data={
            "vault_name": vault,
            "session_id": session,
            "prompt": prompt,
            "model": "session-ops-model",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["task"]["task_id"]


async def wait_for_status(app: Application, task_id: str, expected: str) -> None:
    async with asyncio.timeout(30):
        while True:
            response = await app.request_async(f"/api/tasks/{task_id}")
            assert response.status_code == 200, response.text
            actual = response.json()["status"]
            if actual == expected:
                return
            assert actual not in {
                "completed",
                "failed",
                "cancelled",
                "timed_out",
            }, response.text
            await asyncio.sleep(0.05)


def event_payloads(body: str) -> list[dict]:
    return [
        json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")
    ]
