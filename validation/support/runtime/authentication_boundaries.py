"""Real ASGI transport input and explicit monotonic time input for auth policy."""


async def websocket_result(application, headers=()):
    sent = []

    async def receive():
        return {"type": "websocket.connect"}

    async def send(message):
        sent.append(message)

    await application(
        {
            "type": "websocket",
            "asgi": {"version": "3.0"},
            "scheme": "wss",
            "path": "/api/status",
            "raw_path": b"/api/status",
            "root_path": "",
            "query_string": b"",
            "headers": list(headers),
            "client": ("192.0.2.1", 54321),
            "server": ("assistant.example.test", 443),
            "subprotocols": [],
            "state": {},
        },
        receive,
        send,
    )
    return sent
