"""Consume real chat output during execution, then await task finalization."""

import asyncio
from typing import Any

from validation.scenarios.application import Application


async def chat_turn(app: Application, data: dict[str, Any]) -> dict[str, Any]:
    from core.chat.task_execution import CHAT_TASK_EVENT_BUFFER

    response = await app.request_async("/api/chat/tasks", method="POST", data=data)
    assert response.status_code == 200, response.text
    payload = response.json()
    task_id = payload["task"]["task_id"]
    events = []
    text = ""
    terminal = None
    cursor = 0
    async with asyncio.timeout(60):
        while terminal is None:
            buffered = await CHAT_TASK_EVENT_BUFFER.events_after(task_id, cursor)
            for event in buffered:
                cursor = event.sequence
                row = dict(event.data)
                row.setdefault("event", event.event)
                events.append(row)
                for choice in row.get("choices") or []:
                    content = (choice.get("delta") or {}).get("content")
                    if isinstance(content, str):
                        text += content
                if event.is_terminal:
                    terminal = row
                    break
            if terminal is None:
                await asyncio.sleep(0.01)
    task = await app.wait_task(task_id)
    result = {
        "task": task,
        "events": events,
        "terminal_event": terminal,
        "text": text,
        "session_id": payload["session_id"],
    }
    app.evidence.append("chat-turns.jsonl", result)
    return {"start_response": response, **result}
