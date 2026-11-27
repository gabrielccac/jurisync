"""Serve several Jurisync MCP servers from one HTTP process, for ChatGPT connectors.

    jurisync-http                      # pje + advbox on 127.0.0.1:8000
    jurisync-http pje advbox djen --port 9000

Each server answers at ``/<name>/mcp`` (for example ``http://127.0.0.1:8000/pje/mcp``), only to this machine. For
ChatGPT, start the tunnel first and give its host: the server then also answers to that host, under a secret path
printed at start (``JURISYNC_HTTP_SECRET`` keeps it across restarts), and to nothing else.

    jurisync-http pje advbox --public-host abc.trycloudflare.com
"""

import argparse
import sys
from contextlib import AsyncExitStack, asynccontextmanager

from jurisync import registry
from jurisync.core.mcp import http_prefix, http_settings, http_urls
from jurisync.customers import load_env_file

DEFAULT_SERVERS = ("pje", "advbox")


def load(name):
    """Import one server and return ``(MCPServer, close callback or None)``."""
    return registry.get(name).load()


def build_app(names, host="127.0.0.1", public_host=None, prefix=""):
    """One Starlette app with every named server mounted at ``<prefix>/<name>``; returns ``(app, close callbacks)``."""
    from starlette.applications import Starlette
    from starlette.routing import Mount

    apps, closers = {}, []
    for name in names:
        server, close = load(name)
        apps[name] = server.streamable_http_app(
            streamable_http_path="/mcp", transport_security=http_settings(host, public_host), host=host
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

    routes = [Mount(f"{prefix}/{name}", app=sub) for name, sub in apps.items()]
    return Starlette(routes=routes, lifespan=lifespan), closers


def main(argv=None):
    parser = argparse.ArgumentParser(description="Serve Jurisync MCP servers over streamable HTTP, one path each.")
    parser.add_argument("servers", nargs="*", choices=list(registry.CONNECTORS), default=list(DEFAULT_SERVERS))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--public-host", help="the tunnel's host name (e.g. abc.trycloudflare.com); adds a secret path")
    args = parser.parse_args(argv)
    load_env_file()
    import uvicorn

    from jurisync.core.http import enable_request_logging

    enable_request_logging(structured=True)
    names = args.servers or list(DEFAULT_SERVERS)
    prefix = http_prefix(args.host, args.public_host)
    app, closers = build_app(names, args.host, args.public_host, prefix)
    for url in http_urls(args.host, args.port, args.public_host, prefix, [f"/{name}/mcp" for name in names]):
        print(f"MCP em {url}", file=sys.stderr)
    try:
        uvicorn.run(app, host=args.host, port=args.port)
    finally:
        for close in closers:
            close()


if __name__ == "__main__":
    main()
