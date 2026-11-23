"""The hosted HTTP app: OAuth endpoints at the root, every connector at ``/mcp`` and each one at ``/<name>/mcp``.

Per request, ``TenantGate`` resolves the token's tenant, refuses connectors the tenant does not have, and opens a
``credentials.scope`` with that tenant's vault entry. The MCP SDK carries the request's context into the tool call, so
the connector logs in with that office's credentials and the session pool keeps that login apart from other offices.

Each endpoint is an ``Office`` server that delegates to the connector servers: it lists and calls only the tools of the
connectors enabled for the office, with only the portals the office has a login for, and records every call (no
arguments, no results) in the store.
"""

import contextvars
import importlib
import inspect
import logging
import os
import threading
import time
from contextlib import AsyncExitStack, asynccontextmanager, suppress
from urllib.parse import urlsplit

from mcp.server.auth.handlers.metadata import MetadataHandler
from mcp.server.auth.middleware.auth_context import AuthContextMiddleware
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser, BearerAuthBackend, RequireAuthMiddleware
from mcp.server.auth.provider import ProviderTokenVerifier
from mcp.server.auth.routes import (
    AUTHORIZATION_PATH,
    build_metadata,
    build_resource_metadata_url,
    cors_middleware,
    create_auth_routes,
    create_protected_resource_routes,
)
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import AnyHttpUrl, ConfigDict, TypeAdapter
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.authentication import AuthenticationMiddleware
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from jurisync import customers, registry
from jurisync.core import credentials
from jurisync.hosted.oauth import SCOPE, BrowserBinding, Provider, login_routes
from jurisync.hosted.throttle import IP_MAX_FAILURES, Throttle

log = logging.getLogger(__name__)

# Connectors whose accounts come from the office's credential scope: every registry connector. Anything else (filing)
# still reads the server's own environment and would serve every office with one account, so it is refused.
TENANT_READY = tuple(registry.CONNECTORS)
# Tools that only work on the office's computer (PJe drafts: the run folders and the writes stay local).
LOCAL_ONLY_TOOLS = {name: set(connector.local_only_tools) for name, connector in registry.CONNECTORS.items()}
# Writes an office gets only with its own grant (vault entry or customer profile, never the server's environment):
# connector -> (grant variable, tools). Without the grant the office does not see them and a call is refused before
# the connector runs; the connector checks the same grant again. A connector that registers its writes only when
# allowed (Juridiq) lists none: ``hosted_load`` finds them by comparing both builds.
GRANTED_WRITES = {
    name: (connector.write_grant, set(connector.write_tools))
    for name, connector in registry.CONNECTORS.items()
    if connector.write_grant
}
DEV_FLAG = "JURISYNC_HOSTED_DEV"
# Off by default: a token issued without a resource (RFC 8707) is accepted on every connector the office has. Set to 1
# once the log shows that the clients in use send the resource; a token without one then gets a 401.
REQUIRE_RESOURCE_FLAG = "JURISYNC_REQUIRE_TOKEN_RESOURCE"
METADATA_PATH = "/.well-known/oauth-authorization-server"
# Keep "https://host" as written: OAuth clients compare the issuer as an exact string.
EXACT_URL = TypeAdapter(AnyHttpUrl, config=ConfigDict(url_preserve_empty_path=True))
SYSTEMS = {name: connector.label for name, connector in registry.CONNECTORS.items()}
# (tenant row, user e-mail) of the request, set by TenantGate.
caller = contextvars.ContextVar("jurisync_caller", default=None)


class NotEnabled(ToolError):
    pass


def granted(connector, tool):
    """Whether the current office may call this tool: always, unless it is a write that needs the office's grant."""
    grant, tools = GRANTED_WRITES.get(connector, ("", ()))
    return tool not in tools or credentials.env(grant).strip().lower() == "true"


def hosted_load(name):
    """``Connector.load`` with every portal wired (``Office`` narrows them per office), and with the writes a connector
    registers only when allowed: each office's grant decides who sees and calls them."""
    connector = registry.get(name)
    if connector.write_grant and not connector.write_tools:
        server, close = connector.load(allow_writes=True)
        reads = {tool.name for tool in connector.load(allow_writes=False)[0]._tool_manager.list_tools()}
        GRANTED_WRITES[name] = (
            connector.write_grant,
            {tool.name for tool in server._tool_manager.list_tools()} - reads,
        )
        return server, close
    if connector.portals() and connector.attribute == "create_server()":
        create = importlib.import_module(connector.module).create_server
        if "keys" in inspect.signature(create).parameters:
            # Built at start, outside any office: its own choice would be the server's (empty) environment.
            return connector.load(keys=connector.portals())
    return connector.load()


def offered_portals(connector):
    """The portals the current office has a login for; None for a connector without portals."""
    spec = registry.CONNECTORS.get(connector)
    if spec is None or not spec.portals():
        return None
    configured = getattr(importlib.import_module(spec.module), "configured_portals", None)
    return tuple(configured()) if configured else spec.portals()


def portal_field(server, name):
    return ((server._tool_input_schema(name) or {}).get("properties") or {}).get("portal")


def offer(tool, field, offered, label):
    """A copy of ``tool`` whose ``portal`` takes only the offered portals; None when it would take none.

    A copy: the tools of the connector server are shared by every office.
    """
    if field is None:
        return tool if offered else None
    choices = [field["const"]] if "const" in field else field.get("enum") or list(offered)
    keys = [key for key in choices if key in offered]
    if not keys:
        return None
    if "const" in field:
        return tool
    field = {
        **{key: value for key, value in field.items() if key != "default" or value in keys},
        "enum": keys,
        "description": f"{label} portal key, one of the portals configured for this office: {', '.join(keys)}.",
    }
    schema = {**tool.input_schema, "properties": {**tool.input_schema["properties"], "portal": field}}
    return tool.model_copy(update={"input_schema": schema})


class Office(MCPServer):
    """Serves the tools of several connector servers, each call checked against the office and recorded."""

    def __init__(self, servers, store, **options):
        super().__init__(**options)
        self.servers = servers
        self.store = store
        self.owner = {}
        for connector, server in servers.items():
            for tool in server._tool_manager.list_tools():
                if tool.name in self.owner:
                    raise ValueError(f"{tool.name} is served by both {self.owner[tool.name]} and {connector}")
                self.owner[tool.name] = connector

    def _tool_input_schema(self, name):
        connector = self.owner.get(name)
        return None if connector is None else self.servers[connector]._tool_input_schema(name)

    async def list_tools(self):
        tenant, _ = caller.get() or ({"connectors": []}, "")
        tools = []
        for connector, server in self.servers.items():
            if connector not in tenant["connectors"]:
                continue
            offered = offered_portals(connector)
            for tool in await server.list_tools():
                if not granted(connector, tool.name):
                    continue
                if offered is not None:
                    tool = offer(tool, portal_field(server, tool.name), offered, SYSTEMS.get(connector, connector))
                if tool is not None:
                    tools.append(tool)
        return tools

    async def call_tool(self, name, arguments, context=None):
        connector = self.owner.get(name)
        if connector is None:
            raise ToolError(f"Unknown tool: {name}")
        tenant, user = caller.get() or ({"id": "", "connectors": []}, "")
        started, error = time.monotonic(), ""
        try:
            if tenant.get("mode") == "local":
                raise NotEnabled(
                    f"This office uses its local Jurisync install ({tenant.get('reason') or 'local'}); "
                    "nothing is served for it here"
                )
            if connector not in tenant["connectors"]:
                raise NotEnabled(f"{name} is a {SYSTEMS.get(connector, connector)} tool, not enabled for this office")
            if not granted(connector, name):
                raise NotEnabled(
                    f"{name} changes {SYSTEMS.get(connector, connector)}: writes are not enabled for this office"
                )
            offered = offered_portals(connector)
            if offered is not None:
                self._check_portal(connector, name, arguments, offered)
            result = await self.servers[connector].call_tool(name, arguments, context)
            if getattr(result, "is_error", False):
                error = "ToolResult"
            return result
        except BaseException as exc:
            error = type(exc.__cause__ or exc).__name__
            raise
        finally:
            duration = round((time.monotonic() - started) * 1000)
            self.store.log_call(tenant["id"], user, connector, name, error, duration)

    def _check_portal(self, connector, name, arguments, offered):
        label = SYSTEMS.get(connector, connector)
        if not offered:
            raise NotEnabled(f"{name}: no {label} login is configured for this office")
        field = portal_field(self.servers[connector], name)
        if field is None:
            return
        portal = arguments.get("portal") or field.get("const") or field.get("default")
        if portal is not None and portal not in offered:
            raise NotEnabled(
                f"{name}: no login is configured for {portal} on this office; configured: {', '.join(offered)}"
            )


class TenantGate:
    """``connector=None`` is the combined ``/mcp``: every connector passes here and ``Office`` filters per tool."""

    def __init__(self, app, *, connector, store, vault, resource, open_tenant=None, require_resource=False):
        self.app = app
        self.connector = connector
        self.store = store
        self.vault = vault
        self.resource = resource
        self.open_tenant = open_tenant
        self.require_resource = require_resource
        self._logged = set()
        self._saving = threading.Lock()

    def _note_missing_resource(self, token, tenant_id):
        """Once per client and office: tells the operator whether real clients send the resource."""
        key = (token.client_id, tenant_id)
        if key in self._logged:
            return
        if len(self._logged) >= 1000:
            self._logged.clear()
        self._logged.add(key)
        log.info(
            "token without resource accepted on %s (client %s, office %s); %s=1 would refuse it",
            self.connector or "mcp",
            token.client_id,
            tenant_id,
            REQUIRE_RESOURCE_FLAG,
        )

    def _saver(self, tenant_id):
        """``credentials.save`` for this office: a value a connector renewed (ADVBOX device id) goes to its vault."""

        def save(name, value):
            with self._saving:
                values, files = self.vault.get(tenant_id)
                values[name] = value
                self.vault.put(tenant_id, values, files)
            self.store.log(tenant_id, "conector", "credencial renovada", name)

        return save

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        user = scope.get("user")
        token = user.access_token if isinstance(user, AuthenticatedUser) else None
        tenant_id = self.open_tenant or ((token.claims or {}).get("tenant") if token else None)
        tenant = self.store.tenant(tenant_id) if tenant_id else None
        if tenant is None:
            return await JSONResponse({"error": "invalid_token"}, status_code=401)(scope, receive, send)
        if token:
            audience = (token.resource or "").rstrip("/")
            if audience != self.resource and (audience or self.require_resource):
                return await JSONResponse(
                    {"error": "invalid_token", "error_description": "token issued for another resource"},
                    status_code=401,
                )(scope, receive, send)
            if not audience:
                self._note_missing_resource(token, tenant["id"])
        if self.connector and self.connector not in tenant["connectors"]:
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
        user = (token.subject or "") if token else ""
        marker = caller.set((tenant, user))
        try:
            with credentials.scope(tenant["id"], values, files, saver=self._saver(tenant["id"])):
                await self.app(scope, receive, send)
        finally:
            caller.reset(marker)


def transport_security(base_url):
    """Host and Origin values the connectors answer to: the public address, and this machine for the health check.

    A request with no ``Origin`` header (Claude and ChatGPT call from their servers) passes; a browser page on another
    site sends its own origin and is refused.
    """
    parts = urlsplit(base_url)
    local = ("127.0.0.1", "localhost", "[::1]")
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[parts.netloc, f"{parts.hostname}:*", *local, *(f"{host}:*" for host in local)],
        allowed_origins=[
            f"{parts.scheme}://{parts.netloc}",
            *(f"http://{host}" for host in local),
            *(f"http://{host}:*" for host in local),
        ],
    )


async def healthz(_request):
    """For the proxy and the smoke test: the process answers. No sign-in, nothing about the offices."""
    return JSONResponse({"ok": True})


def flag(name):
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


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
        load = hosted_load
    base_url = base_url.rstrip("/")
    issuer = EXACT_URL.validate_python(base_url)
    provider = Provider(store, base_url, issuer=str(issuer))
    verifier = ProviderTokenVerifier(provider)
    registration, revocation = (
        ClientRegistrationOptions(enabled=True, valid_scopes=[SCOPE], default_scopes=[SCOPE]),
        RevocationOptions(enabled=True),
    )
    # The SDK's metadata plus RFC 9207: the redirect back to the client carries ``iss``.
    metadata = build_metadata(issuer, None, registration, revocation)
    metadata.authorization_response_iss_parameter_supported = True

    def replaced(route):
        if route.path == AUTHORIZATION_PATH:
            # Same endpoint, plus the cookie that ties the sign-in to this browser.
            return Route(AUTHORIZATION_PATH, endpoint=BrowserBinding(route.app, provider), methods=["GET", "POST"])
        if route.path == METADATA_PATH:
            endpoint = cors_middleware(MetadataHandler(metadata).handle, ["GET", "OPTIONS"])
            return Route(METADATA_PATH, endpoint=endpoint, methods=["GET", "OPTIONS"])
        return route

    routes = [
        replaced(route)
        for route in create_auth_routes(
            provider=provider,
            issuer_url=issuer,
            client_registration_options=registration,
            revocation_options=revocation,
        )
    ]
    if {AUTHORIZATION_PATH, METADATA_PATH} - {route.path for route in routes}:
        raise RuntimeError("The MCP SDK no longer serves /authorize or the metadata where the sign-in expects them")
    routes += login_routes(provider, Throttle(), Throttle(max_failures=IP_MAX_FAILURES))
    routes.append(Route("/healthz", endpoint=healthz, methods=["GET"]))
    servers, closers = {}, []
    for name in connectors:
        server, close = load(name)
        for tool in LOCAL_ONLY_TOOLS.get(name, ()):
            with suppress(ToolError):  # already removed by an earlier app in this process
                server.remove_tool(tool)
        servers[name] = server
        if close:
            closers.append(close)
    systems = ", ".join(SYSTEMS.get(name, name) for name in servers)
    offices = {
        name: Office({name: server}, store, name=server.name, version=server.version, instructions=server.instructions)
        for name, server in servers.items()
    }
    # One URL per office; tool names already carry their connector, and Office refuses a name served twice.
    offices[""] = Office(
        servers,
        store,
        name="jurisync",
        version="1.0.0",
        instructions=f"Jurisync: {systems}. Each tool name starts with its system; you see the systems your office has.",
    )
    apps = {}
    security = transport_security(base_url)
    require_resource = flag(REQUIRE_RESOURCE_FLAG)
    for name, office in offices.items():
        path = f"/{name}/mcp" if name else "/mcp"
        resource = base_url + path
        routes += create_protected_resource_routes(
            resource_url=AnyHttpUrl(resource),
            authorization_servers=[issuer],
            scopes_supported=[SCOPE],
            resource_name=f"Jurisync {name}" if name else "Jurisync",
        )
        sub = office.streamable_http_app(streamable_http_path="/mcp", transport_security=security)
        apps[path] = sub
        gate = TenantGate(
            sub,
            connector=name or None,
            store=store,
            vault=vault,
            resource=resource,
            open_tenant=open_tenant,
            require_resource=require_resource,
        )
        metadata = build_resource_metadata_url(AnyHttpUrl(resource))
        endpoint = gate if open_tenant else RequireAuthMiddleware(gate, [SCOPE], metadata)
        routes.append(Mount(f"/{name}", app=endpoint) if name else Route("/mcp", endpoint=endpoint))

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
