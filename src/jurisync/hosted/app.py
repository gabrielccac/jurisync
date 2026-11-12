"""The hosted HTTP app: OAuth endpoints at the root, each connector at ``/<name>/mcp`` behind a bearer token.

Per request, ``TenantGate`` resolves the token's tenant, refuses connectors the tenant does not have, and opens a
``credentials.scope`` with that tenant's vault entry. The MCP SDK carries the request's context into the tool call, so
the connector logs in with that office's credentials and the session pool keeps that login apart from other offices.
"""

import os
from contextlib import AsyncExitStack, asynccontextmanager, suppress

from mcp.server.auth.middleware.auth_context import AuthContextMiddleware
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser, BearerAuthBackend, RequireAuthMiddleware
from mcp.server.auth.provider import ProviderTokenVerifier
from mcp.server.auth.routes import build_resource_metadata_url, create_auth_routes, create_protected_resource_routes
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import AnyHttpUrl, ConfigDict, TypeAdapter
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.authentication import AuthenticationMiddleware
from starlette.responses import JSONResponse
from starlette.routing import Mount

from jurisync import customers
from jurisync.core import credentials
from jurisync.hosted.oauth import SCOPE, Provider, login_routes
from jurisync.hosted.throttle import Throttle

# Connectors whose accounts come from the office's credential scope. Anything else (filing) still reads the server's
# own environment and would serve every office with one account, so it is refused.
TENANT_READY = ("pje", "eproc", "djen", "advbox", "juridiq")
# Tools that change a portal stay off the hosted server until writes are designed per tenant. ADVBOX and Juridiq
# writes stay mounted but answer only for an office whose credentials allow them (ADVBOX_ALLOW_WRITES and
# JURIDIQ_ALLOW_WRITES, from its vault entry or its customer profile).
WRITE_TOOLS = {"pje": {"pje_draft_write", "pje_draft_delete"}}
DEV_FLAG = "JURISYNC_HOSTED_DEV"
# Keep "https://host" as written: OAuth clients compare the issuer as an exact string.
EXACT_URL = TypeAdapter(AnyHttpUrl, config=ConfigDict(url_preserve_empty_path=True))


class TenantGate:
    def __init__(self, app, *, connector, store, vault, resource, open_tenant=None):
        self.app = app
        self.connector = connector
        self.store = store
        self.vault = vault
        self.resource = resource
        self.open_tenant = open_tenant

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        user = scope.get("user")
        token = user.access_token if isinstance(user, AuthenticatedUser) else None
        tenant_id = self.open_tenant or ((token.claims or {}).get("tenant") if token else None)
        tenant = self.store.tenant(tenant_id) if tenant_id else None
        if tenant is None:
            return await JSONResponse({"error": "invalid_token"}, status_code=401)(scope, receive, send)
        if token and token.resource and token.resource.rstrip("/") != self.resource:
            return await JSONResponse(
                {"error": "invalid_token", "error_description": "token issued for another resource"}, status_code=401
            )(scope, receive, send)
        if self.connector not in tenant["connectors"]:
            return await JSONResponse(
                {
                    "error": "insufficient_scope",
                    "error_description": f"{self.connector} is not enabled for this office",
                },
                status_code=403,
            )(scope, receive, send)
        values, files = self.vault.get(tenant["id"])
        if tenant["customer"]:
            values.setdefault("JURISYNC_CUSTOMER", tenant["customer"])
            # The profile's pinned values ([env].set: certificate opt-ins, ADVBOX writes) apply as they do locally.
            for key, value in customers.fixed_variables(tenant["customer"]).items():
                values.setdefault(key, value)
        with credentials.scope(tenant["id"], values, files):
            await self.app(scope, receive, send)


def build_app(base_url, store, vault, connectors=("pje",), *, load=None, open_tenant=None):
    """Return ``(app, close callbacks)``. ``load(name)`` returns ``(MCPServer, close)``; defaults to serve.load.

    ``open_tenant`` is for a short test only: the connectors answer without a token and act for that tenant, so anyone
    who knows the URL uses its portal credentials.
    """
    if open_tenant is not None and store.tenant(open_tenant) is None:
        raise ValueError(f"Unknown tenant {open_tenant}")
    if open_tenant is not None and os.environ.get(DEV_FLAG) != "1":
        raise ValueError(f"Serving without sign-in is for development only: set {DEV_FLAG}=1")
    refused = [name for name in connectors if name not in TENANT_READY]
    if refused:
        raise ValueError(f"Not ready for several offices yet: {', '.join(refused)}")
    if load is None:
        from jurisync.install.serve import load
    base_url = base_url.rstrip("/")
    provider = Provider(store, base_url)
    verifier = ProviderTokenVerifier(provider)
    issuer = EXACT_URL.validate_python(base_url)

    routes = create_auth_routes(
        provider=provider,
        issuer_url=issuer,
        client_registration_options=ClientRegistrationOptions(
            enabled=True, valid_scopes=[SCOPE], default_scopes=[SCOPE]
        ),
        revocation_options=RevocationOptions(enabled=True),
    )
    routes += login_routes(provider, Throttle())
    apps, closers = {}, []
    # The public hostname reaches the server through a proxy; the bearer token, not the Host header, guards it.
    security = TransportSecuritySettings(enable_dns_rebinding_protection=False)
    for name in connectors:
        server, close = load(name)
        for tool in WRITE_TOOLS.get(name, ()):
            with suppress(ToolError):  # already removed by an earlier app in this process
                server.remove_tool(tool)
        resource = f"{base_url}/{name}/mcp"
        routes += create_protected_resource_routes(
            resource_url=AnyHttpUrl(resource),
            authorization_servers=[issuer],
            scopes_supported=[SCOPE],
            resource_name=f"Jurisync {name}",
        )
        sub = server.streamable_http_app(streamable_http_path="/mcp", transport_security=security)
        apps[name] = sub
        gate = TenantGate(sub, connector=name, store=store, vault=vault, resource=resource, open_tenant=open_tenant)
        metadata = build_resource_metadata_url(AnyHttpUrl(resource))
        routes.append(Mount(f"/{name}", app=gate if open_tenant else RequireAuthMiddleware(gate, [SCOPE], metadata)))
        if close:
            closers.append(close)

    @asynccontextmanager
    async def lifespan(_app):
        async with AsyncExitStack() as stack:
            for sub in apps.values():
                await stack.enter_async_context(sub.router.lifespan_context(sub))
            yield

    middleware = [
        Middleware(AuthenticationMiddleware, backend=BearerAuthBackend(verifier)),
        Middleware(AuthContextMiddleware),
    ]
    return Starlette(routes=routes, middleware=middleware, lifespan=lifespan), closers
