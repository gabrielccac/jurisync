"""Serve several Jurisync MCP servers from one HTTP process, for ChatGPT connectors.

    jurisync-http                      # pje + advbox on 127.0.0.1:8000
    jurisync-http pje advbox djen --port 9000

Each server answers at ``/<name>/mcp`` (for example ``http://127.0.0.1:8000/pje/mcp``). There is no
authentication: this is for local test installs, reached through a tunnel you control.
"""

import argparse
import importlib
from contextlib import AsyncExitStack, asynccontextmanager
from functools import reduce

from jurisync.core.mcp import http_settings, load_env_file

# name -> (module, attribute holding the server, or "create_server()", attribute path to close on exit)
SERVERS = {
    "pje": ("jurisync.connectors.pje.server", "server", "sessions.close"),
    "advbox": ("jurisync.connectors.advbox.server", "server", "client.close"),
    "djen": ("jurisync.connectors.djen.server", "server", None),
    "eproc": ("jurisync.connectors.eproc.server", "create_server()", "sessions.close"),
    "juridiq": ("jurisync.connectors.juridiq.server", "create_server()", None),
    "inss": ("jurisync.connectors.inss.server", "server", None),
}
DEFAULT_SERVERS = ("pje", "advbox")


def load(name):
    """Import one server and return ``(MCPServer, close callback or None)``."""
    module_name, attribute, closer = SERVERS[name]
    module = importlib.import_module(module_name)
    server = getattr(module, attribute.removesuffix("()"))
    server = server() if attribute.endswith("()") else server
    return server, (reduce(getattr, closer.split("."), module) if closer else None)


def build_app(names, host="127.0.0.1"):
    """One Starlette app with every named server mounted at ``/<name>``; returns ``(app, close callbacks)``."""
    from starlette.applications import Starlette
    from starlette.routing import Mount

    apps, closers = {}, []
    for name in names:
        server, close = load(name)
        apps[name] = server.streamable_http_app(
            streamable_http_path="/mcp", transport_security=http_settings(), host=host
        )
        if close:
            closers.append(close)

    @asynccontextmanager
    async def lifespan(_app):
        # Mounted apps do not run their own lifespans; each needs its session manager started.
        async with AsyncExitStack() as stack:
            for sub in apps.values():
                await stack.enter_async_context(sub.router.lifespan_context(sub))
            yield

    return Starlette(routes=[Mount(f"/{name}", app=sub) for name, sub in apps.items()], lifespan=lifespan), closers


def main(argv=None):
    parser = argparse.ArgumentParser(description="Serve Jurisync MCP servers over streamable HTTP, one path each.")
    parser.add_argument("servers", nargs="*", choices=list(SERVERS), default=list(DEFAULT_SERVERS))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)
    load_env_file()
    import uvicorn

    from jurisync.core.http import enable_request_logging

    enable_request_logging()
    app, closers = build_app(args.servers or list(DEFAULT_SERVERS), args.host)
    try:
        uvicorn.run(app, host=args.host, port=args.port)
    finally:
        for close in closers:
            close()


if __name__ == "__main__":
    main()
