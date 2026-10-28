"""Multipart input and surface event collection for chat API journeys."""

import asyncio


def oversized_image_upload(app, vault_name):
    response = app.controller.get_api_test_client().post(
        "/api/chat/tasks",
        data={
            "vault_name": vault_name,
            "prompt": "Describe this oversized image.",
            "tools": "[]",
            "model": "test",
        },
        files={"images": ("too-large.png", b"x" * (1024 * 1024 + 1), "image/png")},
    )
    app.evidence.append(
        "chat-upload.jsonl",
        {
            "filename": "too-large.png",
            "bytes": 1024 * 1024 + 1,
            "status": response.status_code,
            "response": response.json(),
        },
    )
    return response


async def collect_surface_events(task_id):
    from core.chat.surface_adapter import subscribe_chat_surface_events

    async with asyncio.timeout(30):
        return [event async for event in subscribe_chat_surface_events(task_id)]
