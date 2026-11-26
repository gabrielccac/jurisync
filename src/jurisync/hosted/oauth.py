"""The authorization server MCP clients (Claude, ChatGPT) talk to: dynamic registration, a sign-in page, tokens.

Built on the SDK's ``OAuthAuthorizationServerProvider``; the SDK serves the OAuth endpoints and checks PKCE, redirect
URIs and client authentication. This module decides who the user is (e-mail + password of a tenant user) and records
which tenant every code and token acts for. Access tokens last one hour; refresh tokens 30 days and rotate on use.

Anyone may register a client, so the sign-in is tied to the browser that opened ``/authorize``: that response sets a
per-request cookie and ``/login`` answers only to the browser holding it. A ``/login`` link passed to someone else is
dead. A whole ``/authorize`` link passed to someone else still works, which is why the page names the client and the
host that receives the access.
"""

import hashlib
import hmac
import html
import secrets
import time
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

import anyio
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    RefreshToken,
    RegistrationError,
    TokenError,
    construct_redirect_uri,
)
from mcp.server.transport_security import RequestBodyLimitMiddleware
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse
from starlette.routing import Route, request_response

from jurisync.hosted.throttle import client_address

SCOPE = "jurisync"
ACCESS_TTL = 3600
REFRESH_TTL = 30 * 24 * 3600
CODE_TTL = 300
PENDING_TTL = 600
MAX_PENDING = 2000
MAX_CLIENTS = 500
MAX_CLIENT_INFO = 16 * 1024
PURGE_EVERY = 3600
LOGIN_PATH = "/login"
LOGIN_COOKIE = "jurisync_login_"
MAX_LOGIN_BODY = 64 * 1024
MAX_PASSWORD_CHECKS = 4
# Where Claude and ChatGPT receive the authorization code; any other redirect host gets a warning on the sign-in page.
KNOWN_HOSTS = {"claude.ai", "claude.com", "chatgpt.com", "chat.openai.com"}
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


@dataclass(frozen=True)
class Pending:
    client: OAuthClientInformationFull
    params: AuthorizationParams
    expires: float
    browser: str  # the cookie value only the browser that opened /authorize holds


def login_cookie(request_id):
    """One cookie name per request, so sign-ins started side by side in one browser do not overwrite each other."""
    return LOGIN_COOKIE + hashlib.sha256(request_id.encode("utf-8")).hexdigest()[:16]


class Provider:
    def __init__(self, store, base_url, issuer=None):
        self.store = store
        self.base_url = base_url.rstrip("/")
        # RFC 9207: must be the exact string the metadata document gives as ``issuer``.
        self.issuer = issuer or self.base_url
        self._pending = {}
        self._purged = time.monotonic()
        self.store.purge()

    def housekeeping(self):
        """Drop expired codes and tokens from the store, at most once per ``PURGE_EVERY``."""
        now = time.monotonic()
        if now - self._purged >= PURGE_EVERY:
            self._purged = now
            self.store.purge()

    # Clients

    async def get_client(self, client_id):
        info = await anyio.to_thread.run_sync(self.store.client, client_id)
        return OAuthClientInformationFull.model_validate_json(info) if info else None

    async def register_client(self, client_info):
        info = client_info.model_dump_json()
        if len(info) > MAX_CLIENT_INFO:
            raise RegistrationError("invalid_client_metadata", "client metadata is too large")
        self.store.save_client(client_info.client_id, info)
        self.store.prune_clients(MAX_CLIENTS)

    # Authorization: park the request, send the browser to the sign-in page

    async def authorize(self, client, params):
        self.housekeeping()
        self._expire_pending()
        while len(self._pending) >= MAX_PENDING:
            del self._pending[next(iter(self._pending))]  # oldest first
        request_id = secrets.token_urlsafe(32)
        self._pending[request_id] = Pending(client, params, time.time() + PENDING_TTL, secrets.token_urlsafe(32))
        return f"{self.base_url}{LOGIN_PATH}?request={request_id}"

    def _expire_pending(self):
        now = time.time()
        for key in [key for key, entry in self._pending.items() if entry.expires < now]:
            del self._pending[key]

    def browser_secret(self, request_id):
        entry = self._pending.get(request_id or "")
        return entry.browser if entry else None

    def pending(self, request_id, browser):
        """The parked request, only for the browser that opened ``/authorize`` (it holds the cookie value)."""
        self._expire_pending()
        entry = self._pending.get(request_id or "")
        if entry is None or not hmac.compare_digest(entry.browser.encode(), (browser or "").encode("utf-8")):
            return None
        return entry

    def complete(self, request_id, email, tenant):
        """Issue the authorization code for a signed-in user and return where to send the browser.

        A request is used once: None when it is gone (expired, or completed by a parallel submit).
        """
        entry = self._pending.pop(request_id, None)
        if entry is None or entry.expires < time.time():
            return None
        client_id, params = entry.client.client_id, entry.params
        code = secrets.token_urlsafe(32)
        record = AuthorizationCode(
            code=code,
            scopes=params.scopes or [SCOPE],
            expires_at=time.time() + CODE_TTL,
            client_id=client_id,
            code_challenge=params.code_challenge,
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=params.resource,
            subject=email,
        )
        self.store.save_code(code, tenant, record.model_dump(mode="json"), record.expires_at)
        return construct_redirect_uri(str(params.redirect_uri), code=code, state=params.state, iss=self.issuer)

    async def load_authorization_code(self, client, authorization_code):
        found = await anyio.to_thread.run_sync(self.store.code, authorization_code)
        if found is None:
            return None
        code = AuthorizationCode.model_validate(found[1])
        return code if code.client_id == client.client_id else None

    async def exchange_authorization_code(self, client, authorization_code):
        return await anyio.to_thread.run_sync(self._exchange_code, client.client_id, authorization_code)

    def _exchange_code(self, client_id, authorization_code):
        found = self.store.code(authorization_code.code)
        self.store.delete_code(authorization_code.code)
        if found is None:
            raise TokenError("invalid_grant", "authorization code expired or already used")
        return self._issue(
            client_id,
            found[0],
            authorization_code.subject,
            authorization_code.scopes,
            authorization_code.resource,
        )

    # Tokens

    def _issue(self, client_id, tenant, subject, scopes, resource):
        self.housekeeping()
        access, refresh, pair = secrets.token_urlsafe(32), secrets.token_urlsafe(32), secrets.token_hex(16)
        now = int(time.time())
        common = {
            "client_id": client_id,
            "tenant": tenant,
            "subject": subject,
            "scopes": scopes,
            "resource": resource,
            "pair": pair,
        }
        self.store.save_token(access, kind="access", expires=now + ACCESS_TTL, **common)
        self.store.save_token(refresh, kind="refresh", expires=now + REFRESH_TTL, **common)
        return OAuthToken(access_token=access, expires_in=ACCESS_TTL, refresh_token=refresh, scope=" ".join(scopes))

    async def load_refresh_token(self, client, refresh_token):
        row = await anyio.to_thread.run_sync(self.store.token, refresh_token, "refresh")
        if row is None or row["client_id"] != client.client_id:
            return None
        return RefreshToken(
            token=refresh_token,
            client_id=row["client_id"],
            scopes=row["scopes"],
            expires_at=row["expires"],
            resource=row["resource"],
            subject=row["subject"],
        )

    async def exchange_refresh_token(self, client, refresh_token, scopes):
        return await anyio.to_thread.run_sync(self._exchange_refresh, client.client_id, refresh_token, scopes)

    def _exchange_refresh(self, client_id, refresh_token, scopes):
        row = self.store.token(refresh_token.token, "refresh")
        if row is None:
            raise TokenError("invalid_grant", "refresh token expired or revoked")
        if self.store.tenant(row["tenant"]) is None:
            raise TokenError("invalid_grant", "account no longer exists")
        self.store.delete_pair(refresh_token.token)
        return self._issue(client_id, row["tenant"], row["subject"], scopes or row["scopes"], row["resource"])

    async def load_access_token(self, token):
        row = await anyio.to_thread.run_sync(self.store.token, token, "access")
        if row is None:
            return None
        return AccessToken(
            token=token,
            client_id=row["client_id"],
            scopes=row["scopes"],
            expires_at=row["expires"],
            resource=row["resource"],
            subject=row["subject"],
            claims={"tenant": row["tenant"]},
        )

    async def revoke_token(self, token):
        await anyio.to_thread.run_sync(self.store.delete_pair, token.token)


class BrowserBinding:
    """Wraps the SDK's ``/authorize`` endpoint: its redirect to the sign-in page also sets that request's cookie.

    The SDK builds the redirect from the URL ``Provider.authorize`` returns and offers no way to add a header, so the
    cookie is added here, on the same response. Whoever follows the redirect without it (a forwarded ``/login`` link)
    gets the "start again" page.
    """

    def __init__(self, app, provider):
        self.app = app
        self.provider = provider
        self.prefix = f"{provider.base_url}{LOGIN_PATH}?"
        self.secure = "; Secure" if provider.base_url.startswith("https://") else ""

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def send_with_cookie(message):
            if message["type"] == "http.response.start":
                cookie = self.cookie(dict(message.get("headers", ())).get(b"location", b"").decode("latin-1"))
                if cookie:
                    message = {**message, "headers": [*message["headers"], (b"set-cookie", cookie.encode("latin-1"))]}
            await send(message)

        await self.app(scope, receive, send_with_cookie)

    def cookie(self, location):
        if not location.startswith(self.prefix):
            return None
        request_id = parse_qs(urlsplit(location).query).get("request", [""])[0]
        secret = self.provider.browser_secret(request_id)
        if secret is None:
            return None
        return (
            f"{login_cookie(request_id)}={secret}; Path={LOGIN_PATH}; Max-Age={PENDING_TTL}; HttpOnly; SameSite=Lax"
            f"{self.secure}"
        )


def redirect_target(redirect_uri):
    """``(what to show, known)`` for the address that receives the authorization code."""
    parts = urlsplit(str(redirect_uri))
    host = parts.hostname or ""
    known = (parts.scheme == "https" and host in KNOWN_HOSTS) or (
        parts.scheme in ("http", "https") and host in LOCAL_HOSTS
    )
    return (host if host and parts.scheme in ("http", "https") else f"{parts.scheme}://{host}"), known


def client_label(client):
    name = " ".join("".join(char for char in (client.client_name or "") if char.isprintable()).split())[:60]
    return f"O aplicativo <strong>“{html.escape(name)}”</strong>" if name else "Um aplicativo sem nome"


PAGE = """<!doctype html><html lang="pt-BR"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Jurisync</title>
<body style="font-family:system-ui;max-width:26rem;margin:4rem auto;padding:0 1rem">
<h1>Entrar no Jurisync</h1>
<p>{client} receberá acesso aos processos do seu escritório pelo Jurisync. Depois de entrar, você será enviado de
volta para <strong>{host}</strong>.</p>
{warning}
<p>Entre com o e-mail e a senha do seu escritório.</p>
{error}
<form method="post" action="/login">
<input type="hidden" name="request" value="{request}">
<p><label>E-mail<br><input name="email" type="email" required autofocus autocomplete="username"
 style="width:100%;padding:.6rem;font-size:1rem"></label></p>
<p><label>Senha<br><input name="password" type="password" required autocomplete="current-password"
 style="width:100%;padding:.6rem;font-size:1rem"></label></p>
<p><button style="padding:.6rem 1.2rem;font-size:1rem">Entrar e autorizar</button></p>
</form></body></html>"""

WARNING = (
    "<p style='border:2px solid #b00020;color:#b00020;padding:.8rem'><strong>Atenção:</strong> {host} não é um "
    "endereço conhecido do Claude nem do ChatGPT. Continue somente se foi você quem iniciou esta conexão agora, em um "
    "aplicativo em que confia. Se você recebeu este link de outra pessoa, feche esta página.</p>"
)

PAGE_HEADERS = {
    "Cache-Control": "no-store",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "frame-ancestors 'none'",
    "Referrer-Policy": "no-referrer",
}

# One answer for every reason a request cannot go on (expired, used, unknown, or opened in another browser).
EXPIRED = HTMLResponse(
    "<!doctype html><meta charset='utf-8'><p style='font-family:system-ui;margin:4rem auto;max-width:26rem'>"
    "Este pedido de acesso expirou ou não foi iniciado neste navegador. Volte ao aplicativo e conecte de novo.</p>",
    status_code=400,
    headers=PAGE_HEADERS,
)


def login_routes(provider, throttle, address_throttle):
    checking = [0]  # password checks running in threads right now

    def page(request_id, entry, error="", status_code=200):
        host, known = redirect_target(entry.params.redirect_uri)
        host = html.escape(host)
        message = f"<p style='color:#b00020'>{html.escape(error)}</p>" if error else ""
        return HTMLResponse(
            PAGE.format(
                client=client_label(entry.client),
                host=host,
                warning="" if known else WARNING.format(host=f"<strong>{host}</strong>"),
                request=html.escape(request_id, quote=True),
                error=message,
            ),
            status_code=status_code,
            headers=PAGE_HEADERS,
        )

    def parked(request: Request, request_id):
        return provider.pending(request_id, request.cookies.get(login_cookie(request_id)))

    async def show(request: Request):
        request_id = request.query_params.get("request", "")
        entry = parked(request, request_id)
        return page(request_id, entry) if entry else EXPIRED

    async def submit(request: Request):
        form = await request.form()
        request_id = str(form.get("request", ""))
        entry = parked(request, request_id)
        if entry is None:
            return EXPIRED
        email = str(form.get("email", "")).strip().lower()
        address = client_address(
            request.client.host if request.client else "", request.headers.get("x-forwarded-for", "")
        )
        if throttle.blocked(email) or address_throttle.blocked(address):
            return page(request_id, entry, "Muitas tentativas. Aguarde 15 minutos e tente de novo.", 429)
        if checking[0] >= MAX_PASSWORD_CHECKS:
            return page(request_id, entry, "Servidor ocupado. Tente de novo em instantes.", 503)
        checking[0] += 1
        try:
            # scrypt takes a while by design: off the event loop, so tool calls keep flowing meanwhile.
            tenant = await anyio.to_thread.run_sync(provider.store.authenticate, email, str(form.get("password", "")))
        finally:
            checking[0] -= 1
        if tenant is None:
            throttle.fail(email)
            address_throttle.fail(address)
            return page(request_id, entry, "E-mail ou senha incorretos.")
        throttle.clear(email)
        target = provider.complete(request_id, email, tenant)
        if target is None:
            return EXPIRED
        response = RedirectResponse(target, status_code=302, headers={"Cache-Control": "no-store"})
        response.delete_cookie(login_cookie(request_id), path=LOGIN_PATH)
        return response

    return [
        Route(LOGIN_PATH, show, methods=["GET"]),
        Route(LOGIN_PATH, RequestBodyLimitMiddleware(request_response(submit), MAX_LOGIN_BODY), methods=["POST"]),
    ]
