"""Real local MCP HTTP sessions with server-side workload barriers and notifications."""

from __future__ import annotations

import asyncio
import json
import socket
from collections import Counter
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import uvicorn
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.types import TextContent, Tool
from starlette.applications import Starlette
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route

from validation.scenarios.application import Application


@dataclass
class MCPWorkload:
    requests: list = field(default_factory=list)
    calls: list = field(default_factory=list)
    list_entered: asyncio.Event = field(default_factory=asyncio.Event)
    list_release: asyncio.Event = field(default_factory=asyncio.Event)
    reject_status: int | None = None
    required_authorization: str | None = None
    resource_metadata_url: str | None = None
    malformed_initialize: bool = False
    initialize_delay: float = 0
    notify_on_list: bool = False
    url: str = ""

    def initialize_count(self):
        return sum(row.get("rpc_method") == "initialize" for row in self.requests)

    def closed_sessions(self):
        return Counter(
            row["session_id"] for row in self.requests if row["method"] == "DELETE"
        )


@asynccontextmanager
async def mcp_http_server(app: Application, *, tls=None, extra_routes=()):
    workload = MCPWorkload()
    workload.list_release.set()
    server = Server("assistantmd-real-mcp-input")

    @server.list_tools()
    async def tools():
        workload.list_entered.set()
        await workload.list_release.wait()
        if workload.notify_on_list:
            await server.request_context.session.send_tool_list_changed()
            await asyncio.sleep(0.05)
        return [
            Tool(
                name="search_messages",
                description="Search Gmail messages matching a query.",
                inputSchema={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            ),
            Tool(
                name="archive_message",
                description="Archive one message.",
                inputSchema={"type": "object"},
            ),
        ]

    @server.call_tool()
    async def call(name, arguments):
        workload.calls.append((name, arguments))
        return [
            TextContent(
                type="text", text=f"matching message for {arguments.get('query', '')}"
            )
        ]

    sessions = StreamableHTTPSessionManager(server, json_response=True)

    class Handler:
        async def __call__(self, scope, receive, send):
            body = b""
            more = True
            while more:
                message = await receive()
                body += message.get("body", b"")
                more = message.get("more_body", False)
            try:
                request = json.loads(body) if body else {}
            except ValueError:
                request = {}
            headers = dict(scope.get("headers", []))
            row = {
                "method": scope["method"],
                "host": headers.get(b"host", b"").decode(),
                "rpc_method": request.get("method"),
                "authorization": headers.get(b"authorization", b"").decode(),
                "session_id": headers.get(b"mcp-session-id", b"").decode(),
            }
            workload.requests.append(row)
            if (
                workload.required_authorization
                and row["authorization"] != workload.required_authorization
            ):
                await JSONResponse(
                    {"error": "authorization required"},
                    status_code=401,
                    headers={
                        "WWW-Authenticate": f'Bearer resource_metadata="{workload.resource_metadata_url}"'
                    },
                )(scope, receive, send)
                return
            if workload.reject_status:
                await JSONResponse(
                    {"error": "credential-bearing raw transport failure"},
                    status_code=workload.reject_status,
                )(scope, receive, send)
                return
            if request.get("method") == "initialize":
                if workload.initialize_delay:
                    await asyncio.sleep(workload.initialize_delay)
                if workload.malformed_initialize:
                    await JSONResponse(
                        {
                            "jsonrpc": "2.0",
                            "id": request.get("id"),
                            "result": {
                                "protocolVersion": 17,
                                "malformed-secret-detail": "private",
                            },
                        }
                    )(scope, receive, send)
                    return
            consumed = False

            async def replay():
                nonlocal consumed
                if not consumed:
                    consumed = True
                    return {"type": "http.request", "body": body, "more_body": False}
                return await receive()

            async def capture(message):
                if message["type"] == "http.response.start":
                    for name, value in message.get("headers", []):
                        if name.lower() == b"mcp-session-id":
                            row["response_session_id"] = value.decode()
                await send(message)

            await sessions.handle_request(scope, replay, capture)

    @asynccontextmanager
    async def lifespan(_app):
        async with sessions.run():
            yield

    asgi = Starlette(
        routes=[
            Route("/mcp", Handler(), methods=["GET", "POST", "DELETE"]),
            Route("/plain", lambda request: PlainTextResponse("ok")),
            *extra_routes,
        ],
        lifespan=lifespan,
    )
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    sock.setblocking(False)
    port = sock.getsockname()[1]
    workload.url = (
        f"https://localhost:{port}/mcp" if tls else f"http://127.0.0.1:{port}/mcp"
    )
    options = (
        {"ssl_certfile": str(tls.certificate), "ssl_keyfile": str(tls.key)}
        if tls
        else {}
    )
    config = uvicorn.Config(asgi, log_level="error", lifespan="on", **options)
    if tls:
        config.load()
        config.ssl.set_servername_callback(
            lambda sock, name, context: tls.names.append(name)
        )
    runner = uvicorn.Server(config)
    serving = asyncio.create_task(runner.serve(sockets=[sock]))
    try:
        async with asyncio.timeout(5):
            while not runner.started:
                if serving.done():
                    await serving
                await asyncio.sleep(0.01)
        yield workload
    finally:
        workload.list_release.set()
        runner.should_exit = True
        await asyncio.wait_for(serving, timeout=5)
        sock.close()
        app.evidence.save_json(
            "mcp-http-input.json",
            {"requests": workload.requests, "calls": workload.calls},
        )
