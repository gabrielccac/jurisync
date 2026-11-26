"""Smoke test of a running Jurisync hosted server, as Claude or ChatGPT see it. No portal login is made.

    python deploy/smoke_test.py https://mcp.example.com.br
    python deploy/smoke_test.py https://mcp.example.com.br --plan rollout/plan.json \
        --user advogada@victor.test=/root/victor.password --user advogada@herica.test=/root/herica.password

Without users: /healthz, the OAuth metadata, the protected resources of /tribunais/mcp and /mcp and the 401 a client
gets before signing in.
With users (password read from a file, never from the command line): dynamic client registration, the sign-in page,
PKCE code exchange, then MCP initialize + tools/list on /tribunais/mcp (court systems and DJEN only), on the
/<system>/mcp of each management system the office has (ADVBOX, Juridiq: that system's tools only) and on the
deprecated /mcp. With --plan, the systems must be exactly the office's, and a tool of a system the office lacks must
be refused (tenant isolation). With --call-saved-searches, djen_list_saved_searches runs for each user (reads the
office's own profile; no portal).
Exit status 1 when any check fails.
"""

import argparse
import base64
import hashlib
import json
import secrets
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

CALLBACK = "http://127.0.0.1:9/callback"
PROTOCOL = "2025-06-18"
# A tool per system, used to prove that an office without that system is refused.
PROBES = {
    "pje": "pje_list_watched_processes",
    "eproc": "eproc_list_processes",
    "esaj": "esaj_get_session",
    "projudi": "projudi_get_process",
    "stj": "stj_get_process",
    "tucujuris": "tucujuris_list_processes",
    "jpe": "jpe_list_processes",
    "advbox": "advbox_list_members",
    "juridiq": "juridiq_list_users",
}
# Served together at /tribunais/mcp (registry ``group``); any other system has its own /<name>/mcp.
COURTS = {"pje", "eproc", "esaj", "projudi", "stj", "tucujuris", "jpe", "djen"}
failures = []


class Response:
    def __init__(self, status, headers, body):
        self.status_code = status
        self.headers = {key.lower(): value for key, value in headers.items()}
        self.text = body.decode("utf-8", "replace")

    def json(self):
        return json.loads(self.text)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class Client:
    """Just enough HTTP for the check, with the standard library only: no redirects followed, no cookies kept
    except the sign-in cookie /authorize sets for /login."""

    def __init__(self, base, timeout=60):
        self.base = base
        self.timeout = timeout
        self.cookies = {}
        self.opener = urllib.request.build_opener(NoRedirect)

    def request(self, method, path, *, params=None, json_body=None, data=None, headers=None):
        url = self.base + path + (("?" + urlencode(params)) if params else "")
        headers = dict(headers or {})
        body = None
        if json_body is not None:
            body, headers["Content-Type"] = json.dumps(json_body).encode(), "application/json"
        elif data is not None:
            body, headers["Content-Type"] = urlencode(data).encode(), "application/x-www-form-urlencoded"
        if self.cookies:
            headers["Cookie"] = "; ".join(f"{key}={value}" for key, value in self.cookies.items())
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=self.timeout) as answer:
                response = Response(answer.status, answer.headers, answer.read())
        except urllib.error.HTTPError as error:
            response = Response(error.code, error.headers, error.read())
        for name, value in response.headers.items():
            if name == "set-cookie":
                key, _, rest = value.partition("=")
                self.cookies[key.strip()] = rest.split(";", 1)[0]
        return response

    def get(self, path, **options):
        return self.request("GET", path, **options)

    def post(self, path, json=None, data=None, headers=None):
        return self.request("POST", path, json_body=json, data=data, headers=headers)


def check(ok, message):
    print(("ok    " if ok else "FALHA ") + message)
    if not ok:
        failures.append(message)
    return ok


def pkce():
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


# Tools only an office's own computer serves (registry ``local_only_tools``).
LOCAL_ONLY = {
    "pje_fill_draft",
    "pje_delete_draft",
    "pje_get_draft_status",
    "pje_get_draft",
    "pje_search_pending_drafts",
}


def public_checks(client, base):
    health = client.get("/healthz")
    check(health.status_code == 200 and health.json().get("ok") is True, f"/healthz ({health.status_code})")
    metadata = client.get("/.well-known/oauth-authorization-server")
    check(metadata.status_code == 200, f"metadados OAuth ({metadata.status_code})")
    if metadata.status_code == 200:
        check(metadata.json().get("issuer", "").rstrip("/") == base, "issuer igual ao endereço público")
    for path in ("/tribunais/mcp", "/mcp"):
        resource = client.get(f"/.well-known/oauth-protected-resource{path}")
        check(
            resource.status_code == 200 and resource.json().get("resource") == f"{base}{path}",
            f"recurso protegido {path}",
        )
        anonymous = client.post(path, json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
        check(
            anonymous.status_code == 401 and "resource_metadata" in anonymous.headers.get("www-authenticate", ""),
            f"{path} sem token responde 401 com resource_metadata",
        )


def sign_in(client, base, email, password, path="/mcp"):
    registered = client.post(
        "/register",
        json={
            "client_name": "Jurisync smoke test",
            "redirect_uris": [CALLBACK],
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
        },
    )
    registered.raise_for_status()
    client_id = registered.json()["client_id"]
    verifier, challenge = pkce()
    authorize = client.get(
        "/authorize",
        params={
            "response_type": "code",
            "client_id": client_id,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "redirect_uri": CALLBACK,
            "state": secrets.token_urlsafe(8),
            "resource": f"{base}{path}",
        },
    )
    if authorize.status_code != 302:
        raise RuntimeError(f"/authorize answered {authorize.status_code}")
    request_id = parse_qs(urlsplit(authorize.headers["location"]).query)["request"][0]
    login = client.post("/login", data={"request": request_id, "email": email, "password": password})
    if login.status_code != 302:
        raise RuntimeError(f"sign-in refused ({login.status_code})")
    code = parse_qs(urlsplit(login.headers["location"]).query)["code"][0]
    token = client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "code_verifier": verifier,
            "redirect_uri": CALLBACK,
        },
    )
    token.raise_for_status()
    return token.json()["access_token"]


class Session:
    def __init__(self, client, token, path="/mcp"):
        self.client = client
        self.path = path
        self.headers = {"Authorization": f"Bearer {token}", "Accept": "application/json, text/event-stream"}
        self.ids = iter(range(1, 10_000))
        self.send(
            "initialize",
            {"protocolVersion": PROTOCOL, "capabilities": {}, "clientInfo": {"name": "smoke", "version": "1"}},
        )
        self.send("notifications/initialized", notify=True)

    def send(self, method, params=None, notify=False):
        payload = {"jsonrpc": "2.0", "method": method, **({"params": params} if params is not None else {})}
        if not notify:
            payload["id"] = next(self.ids)
        response = self.client.post(self.path, json=payload, headers=self.headers)
        response.raise_for_status()
        if "mcp-session-id" in response.headers:
            self.headers["mcp-session-id"] = response.headers["mcp-session-id"]
        for line in response.text.splitlines():
            if line.startswith("data:"):
                return json.loads(line[5:])
        return json.loads(response.text) if response.text.strip().startswith("{") else None

    def tools(self):
        return [tool["name"] for tool in self.send("tools/list")["result"]["tools"]]

    def call(self, name, arguments=None):
        return self.send("tools/call", {"name": name, "arguments": arguments or {}})["result"]


def endpoint_checks(client, base, email, password, courts, management):
    """/tribunais/mcp lists the office's court systems and nothing else; each /<system>/mcp only that system."""
    for path, expected in (("/tribunais/mcp", courts), *((f"/{name}/mcp", [name]) for name in management)):
        if not expected:
            continue
        try:
            tools = Session(client, sign_in(client, base, email, password, path), path).tools()
        except (RuntimeError, OSError, KeyError) as error:
            check(False, f"{email}: sessão MCP em {path} ({type(error).__name__}: {error})")
            continue
        systems = sorted({name.split("_", 1)[0] for name in tools})
        check(systems == expected, f"{email}: {path} com {len(tools)} ferramentas de {expected} ({', '.join(systems)})")


def office_of(plan, email):
    for office in plan["offices"]:
        if email in [user.strip().lower() for user in office.get("users", [])]:
            return office
    return None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("base_url")
    parser.add_argument("--user", action="append", default=[], metavar="EMAIL=PASSWORD_FILE")
    parser.add_argument("--plan", help="plan.json of the rollout: expected systems per office")
    parser.add_argument("--call-saved-searches", action="store_true")
    args = parser.parse_args(argv)
    base = args.base_url.rstrip("/")
    plan = json.loads(Path(args.plan).read_text(encoding="utf-8")) if args.plan else None
    client = Client(base)
    public_checks(client, base)
    seen = {}
    for entry in args.user:
        email, _, password_file = entry.partition("=")
        email = email.strip().lower()
        password = Path(password_file).read_text(encoding="utf-8").strip()
        try:
            session = Session(client, sign_in(client, base, email, password))
        except (RuntimeError, OSError, KeyError) as error:
            check(False, f"{email}: entrar e abrir sessão MCP ({type(error).__name__}: {error})")
            continue
        tools = session.tools()
        systems = sorted({name.split("_", 1)[0] for name in tools})
        check(bool(tools), f"{email}: {len(tools)} ferramentas ({', '.join(systems)})")
        seen[email] = set(tools)
        check(not LOCAL_ONLY & set(tools), f"{email}: rascunhos do PJe fora da lista")
        office = office_of(plan, email) if plan else None
        if office is not None:
            expected = sorted(office["connectors"])
            check(systems == expected, f"{email} ({office['tenant']}): sistemas = conectores do plano {expected}")
            for system, probe in PROBES.items():
                # Only a tool the office does not have: the server refuses it before any portal is reached.
                if system in office["connectors"] or probe in tools:
                    continue
                refused = session.call(probe)
                text = refused.get("content", [{}])[0].get("text", "")
                check(
                    refused.get("isError") and "not enabled for this office" in text,
                    f"{email}: {probe} recusado (escritório sem {system})",
                )
                break
        management = sorted(set(office["connectors"]) - COURTS) if office else sorted(set(systems) - COURTS)
        courts = sorted(set(office["connectors"]) & COURTS) if office else sorted(set(systems) & COURTS)
        endpoint_checks(client, base, email, password, courts, management)
        if args.call_saved_searches and "djen_list_saved_searches" in tools:
            result = session.call("djen_list_saved_searches")
            data = result.get("structuredContent") or {}
            check(
                not result.get("isError"),
                f"{email}: djen_list_saved_searches ({len(data.get('searches', []))} busca(s))",
            )
    if len(seen) > 1 and plan:
        by_office = {}
        for email, tools in seen.items():
            office = office_of(plan, email)
            by_office.setdefault(office["tenant"] if office else email, set()).update(tools)
        print(f"isolamento: {len(by_office)} escritório(s) com listas próprias")
    print(f"\n{len(failures)} falha(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
