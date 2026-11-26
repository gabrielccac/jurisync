"""The operator's read-only API at ``/monitor``: what is up, the open sessions and the offices' activity.

Served only when the service has ``JURISYNC_MONITOR_TOKEN`` (systemd credential): every request carries it as a bearer
token. It is the operator's, never an office's, and answers with names, states, counts and times: no credential, cookie,
token, argument, result or process number is stored or returned.

    GET /monitor/status                                     the process, the systems and the totals
    GET /monitor/portals                                    each portal: reachable now, last logins, last calls
    GET /monitor/sessions                                   portal sessions and cool-downs; signed-in users
    GET /monitor/calls?office=&connector=&portal=&errors=1&limit=   the latest tool calls
    GET /monitor/usage?hours=24&office=                     calls, errors and durations per office, system, portal and tool

Reachability is an anonymous GET of each served portal's entry page every ``JURISYNC_PROBE_SECONDS`` (600; 0 turns it
off): no login, no redirect followed. A portal that answers may still refuse a login: ``logins_24h`` tells that part.
"""

import asyncio
import contextlib
import hmac
import logging
import time
from concurrent.futures import ThreadPoolExecutor

from starlette.responses import JSONResponse
from starlette.routing import Route

from jurisync import portals, registry
from jurisync.core import credentials, session_pool
from jurisync.core.http import impersonate_for
from jurisync.hosted import access
from jurisync.hosted.throttle import Throttle, client_address

log = logging.getLogger(__name__)

TOKEN = "JURISYNC_MONITOR_TOKEN"
PROBE_SECONDS = 600
PROBE_TIMEOUT = 15
PROBE_WORKERS = 4
MAX_CALLS = 500
MAX_HOURS = 24 * 90
LOGIN_HOURS = 24
UP, REFUSING, DOWN, UNKNOWN = "no ar", "recusando", "fora do ar", "não verificado"
NO_STORE = {"Cache-Control": "no-store"}


def probe_state(status):
    """``status`` is the HTTP status of the portal's address, None when it did not answer."""
    if status is None or status >= 500:
        return DOWN
    # A WAF answers 403/429 to an address it does not like; the login may still go through (or not).
    return REFUSING if status in (403, 429) else UP


def entries(config):
    """The addresses to try, in order: TRF1's edge closes the connection on every path but the application's."""
    paths = [config.root, "/"]
    if config.system == "pje":
        paths.insert(0, f"{config.root}login.seam")
    return [f"https://{config.host}{path}" for path in dict.fromkeys(paths)]


def fetch(url, impersonate):
    """The HTTP status of ``url``, None when nothing answered."""
    from curl_cffi import requests

    try:
        return requests.get(url, impersonate=impersonate, allow_redirects=False, timeout=PROBE_TIMEOUT).status_code
    except Exception:
        return None


def percentile(ordered, share):
    return ordered[min(len(ordered) - 1, int(len(ordered) * share))] if ordered else None


def summary(rows):
    durations = sorted(row["duration_ms"] for row in rows)
    return {
        "chamadas": len(rows),
        "erros": sum(not row["ok"] for row in rows),
        "mediana_ms": percentile(durations, 0.5),
        "p95_ms": percentile(durations, 0.95),
        "ultima": max(row["at"] for row in rows),
    }


def grouped(rows, *columns):
    groups = {}
    for row in rows:
        groups.setdefault(tuple(row[column] for column in columns), []).append(row)
    listed = [
        {**dict(zip(columns, key, strict=True)), **summary(members)} for key, members in groups.items() if any(key)
    ]
    return sorted(listed, key=lambda item: -item["chamadas"])


def number(request, name, default, highest):
    try:
        value = int(request.query_params.get(name) or default)
    except ValueError:
        value = default
    return max(1, min(value, highest))


class Monitor:
    def __init__(self, store, vault, connectors, base_url, offered_portals, *, get=fetch, clock=time.time):
        self.store = store
        self.vault = vault
        self.connectors = tuple(connectors)
        self.base_url = base_url
        self._offered = offered_portals
        self._get = get
        self._clock = clock
        self.started = int(clock())
        self.reach = {}  # portal key -> {"status", "em", "ms"}
        self._failures = Throttle()

    # What the offices have

    def portals_served(self):
        """portal key -> offices with a login for it, as the server offers them now."""
        served = {}
        for office in self.store.tenants():
            if office["mode"] != "hosted":
                continue
            values, files = self.vault.get(office["id"])
            with access.tenant_scope(office, values, files):
                for name in self.connectors:
                    if name in office["connectors"]:
                        for key in self._offered(name) or ():
                            served.setdefault(key, []).append(office["id"])
        return served

    # Reachability

    def probe(self):
        def one(key):
            config, status = portals.get(key), None
            for url in entries(config):
                began = time.monotonic()
                answered = self._get(url, impersonate_for(config))
                elapsed = round((time.monotonic() - began) * 1000)
                status = status if answered is None else answered
                if probe_state(answered) == UP:
                    break
            return key, {"status": status, "em": int(self._clock()), "ms": elapsed}

        with ThreadPoolExecutor(PROBE_WORKERS) as workers:
            self.reach = dict(workers.map(one, sorted(self.portals_served())))

    async def watch(self, seconds):
        while True:
            try:
                await asyncio.to_thread(self.probe)
            except Exception:
                log.exception("portal probe failed")
            await asyncio.sleep(seconds)

    def reachable(self, key):
        seen = self.reach.get(key)
        if seen is None:
            return {"estado": UNKNOWN}
        return {
            "estado": probe_state(seen["status"]),
            "http": seen["status"],
            "ms": seen["ms"],
            "verificado": seen["em"],
        }

    def record_login(self, portal, error, duration_ms):
        """``session_pool.on_login``: one row per login tried, with the reason when it failed."""
        reason = session_pool.login_reason(error) if error is not None else ""
        self.store.log_login(credentials.tenant(), portal, reason, duration_ms)

    @contextlib.asynccontextmanager
    async def running(self):
        """Record logins and watch the portals while the app runs."""
        session_pool.on_login = self.record_login
        seconds = int(session_pool._env_number("JURISYNC_PROBE_SECONDS", PROBE_SECONDS, int))
        watcher = asyncio.create_task(self.watch(seconds)) if seconds > 0 and self.enabled() else None
        try:
            yield
        finally:
            if session_pool.on_login == self.record_login:
                session_pool.on_login = None
            if watcher is not None:
                watcher.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await watcher

    # Sessions

    def portal_sessions(self):
        sessions, blocked = [], []
        for name in self.connectors:
            if name not in registry.CONNECTORS:
                continue
            pool = registry.CONNECTORS[name].pool()
            if pool is None:
                continue
            active, cooling = pool.snapshot()
            sessions += [{"sistema": name, **row} for row in active]
            blocked += [{"sistema": name, **row} for row in cooling]
        return sessions, blocked

    # Answers

    def status(self):
        served, (sessions, blocked) = self.portals_served(), self.portal_sessions()
        states = [self.reachable(key)["estado"] for key in served]
        offices = self.store.tenants()
        since = self._clock() - 24 * 3600
        recent = self.store.calls_since(since)
        systems = []
        for name in self.connectors:
            keys = [key for key in served if portals.get(key).system == name]
            rows = [row for row in recent if row["connector"] == name]
            systems.append(
                {
                    "sistema": name,
                    "nome": registry.label(name) if name in registry.CONNECTORS else name,
                    "escritorios": sum(name in office["connectors"] for office in offices),
                    "portais": len(keys),
                    "portais_no_ar": sum(self.reachable(key)["estado"] == UP for key in keys),
                    "sessoes_abertas": sum(row["sistema"] == name for row in sessions),
                    "ultimas_24h": summary(rows) if rows else None,
                }
            )
        return {
            "ok": True,
            "endereco": self.base_url,
            "iniciado": self.started,
            "no_ar_ha_s": int(self._clock()) - self.started,
            "escritorios": {
                "no_servidor": sum(office["mode"] == "hosted" for office in offices),
                "so_local": sum(office["mode"] == "local" for office in offices),
            },
            "usuarios_conectados": len({row["usuario"] for row in self.store.signed_in()}),
            "portais": {"total": len(served), **{state: states.count(state) for state in sorted(set(states))}},
            "sessoes_abertas": len(sessions),
            "bloqueios_de_login": len(blocked),
            "chamadas_24h": summary(recent) if recent else None,
            "sistemas": systems,
        }

    def portal_list(self):
        calls, logins = {}, {}
        for row in self.store.last_portal_calls():
            calls.setdefault(row["portal"], []).append(row)
        for row in self.store.logins_since(self._clock() - LOGIN_HOURS * 3600):
            logins.setdefault(row["portal"], []).append(row)
        checks = {office["id"]: self.store.checks(office["id"]) for office in self.store.tenants()}
        answer = []
        for key, offices in sorted(self.portals_served().items()):
            config = portals.get(key)
            tried = logins.get(key, [])
            answer.append(
                {
                    "portal": key,
                    "sistema": config.system,
                    "host": config.host,
                    "acesso": self.reachable(key),
                    "logins_24h": {
                        "ok": sum(not row["reason"] for row in tried),
                        "falhas": sum(bool(row["reason"]) for row in tried),
                        "motivos": sorted({row["reason"] for row in tried if row["reason"]}),
                    },
                    "escritorios": [
                        {
                            "escritorio": office,
                            "ultimo_teste": self._check(checks[office].get(key)),
                            "ultima_chamada": next(
                                (
                                    {"em": row["at"], "ferramenta": row["tool"], "ok": bool(row["ok"])}
                                    for row in calls.get(key, [])
                                    if row["tenant"] == office
                                ),
                                None,
                            ),
                        }
                        for office in offices
                    ],
                }
            )
        return {"portais": answer}

    @staticmethod
    def _check(row):
        return None if row is None else {"em": row["at"], "ok": bool(row["ok"])}

    def sessions(self):
        active, blocked = self.portal_sessions()
        return {"portais": active, "bloqueios": blocked, "usuarios": self.store.signed_in()}

    def calls(self, request):
        wanted = {name: request.query_params.get(name) or "" for name in ("office", "connector", "portal")}
        rows = self.store.recent_calls(
            tenant=wanted["office"],
            connector=wanted["connector"],
            portal=wanted["portal"],
            errors=request.query_params.get("errors") == "1",
            limit=number(request, "limit", 100, MAX_CALLS),
        )
        return {"chamadas": rows}

    def usage(self, request):
        hours = number(request, "hours", 24, MAX_HOURS)
        since = self._clock() - hours * 3600
        office = request.query_params.get("office") or ""
        rows = [row for row in self.store.calls_since(since) if not office or row["tenant"] == office]
        logins = [row for row in self.store.logins_since(since) if not office or row["tenant"] == office]
        return {
            "horas": hours,
            "total": summary(rows) if rows else None,
            "por_escritorio": grouped(rows, "tenant"),
            "por_sistema": grouped(rows, "tenant", "connector"),
            "por_portal": grouped([row for row in rows if row["portal"]], "tenant", "portal"),
            "por_ferramenta": grouped(rows, "tool"),
            "por_usuario": grouped(rows, "tenant", "user"),
            "erros": self._counts([{"erro": row["reason"] or row["error"]} for row in rows if not row["ok"]], "erro"),
            "logins": self._counts([{"resultado": row["reason"] or "ok"} for row in logins], "resultado"),
        }

    @staticmethod
    def _counts(rows, column):
        found = {}
        for row in rows:
            found[row[column]] = found.get(row[column], 0) + 1
        return dict(sorted(found.items(), key=lambda item: -item[1]))

    # HTTP

    @staticmethod
    def enabled():
        return bool(credentials.machine(TOKEN).strip())

    def _guarded(self, answer):
        async def endpoint(request):
            expected = credentials.machine(TOKEN).strip()
            peer = request.client.host if request.client else ""
            address = client_address(peer, request.headers.get("x-forwarded-for", ""))
            if not expected:
                return JSONResponse({"error": "not_found"}, status_code=404)
            if self._failures.blocked(address):
                return JSONResponse({"error": "too_many_requests"}, status_code=429, headers=NO_STORE)
            scheme, _, given = request.headers.get("authorization", "").partition(" ")
            if scheme.lower() != "bearer" or not hmac.compare_digest(given.strip().encode(), expected.encode()):
                self._failures.fail(address)
                return JSONResponse(
                    {"error": "invalid_token"}, status_code=401, headers={**NO_STORE, "WWW-Authenticate": "Bearer"}
                )
            # Off the event loop: the store, the vault and the pools are synchronous.
            return JSONResponse(await asyncio.to_thread(answer, request), headers=NO_STORE)

        return endpoint

    def routes(self):
        answers = {
            "status": lambda _request: self.status(),
            "portals": lambda _request: self.portal_list(),
            "sessions": lambda _request: self.sessions(),
            "calls": self.calls,
            "usage": self.usage,
        }
        return [
            Route(f"/monitor/{name}", endpoint=self._guarded(answer), methods=["GET"])
            for name, answer in answers.items()
        ]
