"""Controlled external OAuth discovery/authorization/token service and real MCP."""

import asyncio
import base64
import hashlib
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlencode, urlsplit

from starlette.responses import JSONResponse, RedirectResponse
from starlette.routing import Route

from validation.scenarios.support.mcp_http_inputs import mcp_http_server

ACCESS = "local-mcp-oauth-access"
REFRESH = "local-mcp-oauth-refresh"
SECRET = "local-mcp-oauth-client-secret"


@dataclass
class OAuthProvider:
    origin: str = ""
    resource: str = ""
    discovery: list = field(default_factory=list)
    authorizations: list = field(default_factory=list)
    tokens: list = field(default_factory=list)
    codes: dict = field(default_factory=dict)
    token_entered: asyncio.Event = field(default_factory=asyncio.Event)
    token_release: asyncio.Event = field(default_factory=asyncio.Event)
    metadata_entered: asyncio.Event = field(default_factory=asyncio.Event)
    metadata_release: asyncio.Event = field(default_factory=asyncio.Event)

    async def resource_metadata(self, request):
        self.discovery.append(request.url.path)
        return JSONResponse(
            {
                "resource": self.resource,
                "authorization_servers": [self.origin],
                "scopes_supported": ["mcp:read"],
            }
        )

    async def metadata(self, request):
        self.discovery.append(request.url.path)
        self.metadata_entered.set()
        await self.metadata_release.wait()
        return JSONResponse(
            {
                "issuer": self.origin,
                "authorization_endpoint": self.origin + "/authorize",
                "token_endpoint": self.origin + "/token",
                "response_types_supported": ["code"],
                "grant_types_supported": ["authorization_code", "refresh_token"],
                "code_challenge_methods_supported": ["S256"],
                "token_endpoint_auth_methods_supported": ["client_secret_post"],
            }
        )

    async def authorize(self, request):
        params = dict(request.query_params)
        self.authorizations.append(params)
        assert params["client_id"] == "local-mcp-client"
        assert params["code_challenge_method"] == "S256"
        assert params["scope"] == "mcp:read"
        assert params["resource"] == self.resource
        code = f"controlled-code-{len(self.codes)}"
        self.codes[code] = params
        return RedirectResponse(
            params["redirect_uri"]
            + "?"
            + urlencode({"code": code, "state": params["state"]}),
            status_code=302,
        )

    async def token(self, request):
        params = parse_qs((await request.body()).decode())
        self.tokens.append(params)
        self.token_entered.set()
        await self.token_release.wait()
        if params.get("grant_type") != ["authorization_code"]:
            return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)
        code = params["code"][0]
        original = self.codes.pop(code, None)
        if original is None:
            return JSONResponse({"error": "invalid_grant"}, status_code=400)
        challenge = (
            base64.urlsafe_b64encode(
                hashlib.sha256(params["code_verifier"][0].encode()).digest()
            )
            .decode()
            .rstrip("=")
        )
        if (
            challenge != original["code_challenge"]
            or params.get("client_secret") != [SECRET]
            or params.get("client_id") != ["local-mcp-client"]
            or params.get("redirect_uri") != [original["redirect_uri"]]
        ):
            return JSONResponse({"error": "invalid_grant"}, status_code=400)
        return JSONResponse(
            {
                "access_token": ACCESS,
                "refresh_token": REFRESH,
                "token_type": "Bearer",
                "expires_in": 3600,
                "scope": "mcp:read",
            }
        )


@asynccontextmanager
async def oauth_provider(app):
    # Only the external provider is controlled. Discovery, PKCE/state, actual
    # HTTP, SDK auth, app coordinator and encrypted token storage remain real.
    provider = OAuthProvider()
    provider.token_release.set()
    provider.metadata_release.set()
    app.evidence.register_secret(ACCESS, REFRESH, SECRET)
    routes = [
        Route("/.well-known/oauth-protected-resource", provider.resource_metadata),
        Route("/.well-known/oauth-protected-resource/mcp", provider.resource_metadata),
        Route("/.well-known/oauth-authorization-server", provider.metadata),
        Route("/authorize", provider.authorize),
        Route("/token", provider.token, methods=["POST"]),
    ]
    async with mcp_http_server(app, extra_routes=routes) as mcp:
        provider.resource = mcp.url
        provider.origin = mcp.url.removesuffix("/mcp")
        mcp.required_authorization = "Bearer " + ACCESS
        mcp.resource_metadata_url = (
            provider.origin + "/.well-known/oauth-protected-resource"
        )
        try:
            yield provider, mcp
        finally:
            provider.token_release.set()
            provider.metadata_release.set()
            app.evidence.save_json(
                "oauth-provider.json",
                {
                    "discovery": provider.discovery,
                    "authorizations": provider.authorizations,
                    "token_requests": provider.tokens,
                },
            )


def create_connection(app, provider):
    from core.identity import LOCAL_USER_AUTHORITY
    from core.mcp import MCPAuthMode, MCPConnectionCreate
    from core.runtime.state import get_runtime_context

    runtime = get_runtime_context()
    connection = runtime.mcp_connections.create_connection_for_authority(
        LOCAL_USER_AUTHORITY,
        MCPConnectionCreate(
            display_name="Local OAuth MCP",
            url=provider.resource,
            allow_private_http=True,
            auth_mode=MCPAuthMode.OAUTH,
            oauth_client_id="local-mcp-client",
            oauth_client_secret=SECRET,
            oauth_scopes=("mcp:read",),
        ),
    )
    return runtime, connection


async def authorize(app, provider, authorization_url):
    import httpx

    async with httpx.AsyncClient(trust_env=False, follow_redirects=False) as client:
        response = await client.get(authorization_url)
    assert response.status_code == 302, response.text
    callback = response.headers["location"]
    query = {
        key: values[0] for key, values in parse_qs(urlsplit(callback).query).items()
    }
    app.evidence.register_secret(query["code"], query["state"])
    return urlsplit(callback).path, query


@dataclass
class OAuthJourney:
    app: object
    runtime: object
    connection: object
    coordinator: object

    async def start(self):
        from core.identity import LOCAL_USER_AUTHORITY

        return await self.coordinator.start(
            authority=LOCAL_USER_AUTHORITY,
            connection_id=self.connection.connection_id,
            redirect_uri=f"https://assistant.example.test/api/system/mcp/connections/{self.connection.connection_id}/oauth/callback",
        )

    async def complete(self, query):
        from core.identity import LOCAL_USER_AUTHORITY

        return await self.coordinator.complete(
            authority=LOCAL_USER_AUTHORITY,
            connection_id=self.connection.connection_id,
            code=query["code"],
            state=query["state"],
        )

    async def status(self):
        from core.identity import LOCAL_USER_AUTHORITY

        return await self.coordinator.status(
            authority=LOCAL_USER_AUTHORITY, connection_id=self.connection.connection_id
        )

    async def disconnect(self):
        from core.identity import LOCAL_USER_AUTHORITY

        await self.coordinator.disconnect(
            authority=LOCAL_USER_AUTHORITY, connection_id=self.connection.connection_id
        )

    def events(self):
        from validation.scenarios.support.mcp_oauth_inputs import activity

        return [
            row
            for row in activity(self.app)
            if row.get("connection_id") == self.connection.connection_id
            and row.get("event", "").startswith("mcp_oauth_")
        ]

    async def wait_for_failure(self):
        async with asyncio.timeout(5):
            while not any(
                row.get("event") == "mcp_oauth_attempt_failed" for row in self.events()
            ):
                await asyncio.sleep(0.01)


@asynccontextmanager
async def authorization_journey(app, provider, **timeouts):
    from core.mcp.oauth import MCPOAuthCoordinator, MCPOAuthTimeouts

    runtime, connection = create_connection(app, provider)
    # This is a real coordinator composed with actual runtime services and
    # supported deadline inputs. No service method or clock is replaced.
    coordinator = MCPOAuthCoordinator(
        connections=runtime.mcp_connections,
        manager=runtime.mcp_manager,
        timeouts=MCPOAuthTimeouts(**timeouts),
    )
    try:
        yield OAuthJourney(app, runtime, connection, coordinator)
    finally:
        await coordinator.shutdown()
