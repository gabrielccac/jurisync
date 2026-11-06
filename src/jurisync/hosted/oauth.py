"""The authorization server MCP clients (Claude, ChatGPT) talk to: dynamic registration, a sign-in page, tokens.

Built on the SDK's ``OAuthAuthorizationServerProvider``; the SDK serves the OAuth endpoints and checks PKCE, redirect
URIs and client authentication. This module decides who the user is (e-mail + password of a tenant user) and records
which tenant every code and token acts for. Access tokens last one hour; refresh tokens 30 days and rotate on use.
"""

import html
import secrets
import time

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    RefreshToken,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse
from starlette.routing import Route

SCOPE = "jurisync"
ACCESS_TTL = 3600
REFRESH_TTL = 30 * 24 * 3600
CODE_TTL = 300
PENDING_TTL = 600


class Provider:
    def __init__(self, store, base_url):
        self.store = store
        self.base_url = base_url.rstrip("/")
        self._pending = {}

    # Clients

    async def get_client(self, client_id):
        info = self.store.client(client_id)
        return OAuthClientInformationFull.model_validate_json(info) if info else None

    async def register_client(self, client_info):
        self.store.save_client(client_info.client_id, client_info.model_dump_json())

    # Authorization: park the request, send the browser to the sign-in page

    async def authorize(self, client, params):
        self._expire_pending()
        request_id = secrets.token_urlsafe(32)
        self._pending[request_id] = (client.client_id, params, time.time() + PENDING_TTL)
        return f"{self.base_url}/login?request={request_id}"

    def _expire_pending(self):
        now = time.time()
        for key in [key for key, (_, _, expires) in self._pending.items() if expires < now]:
            del self._pending[key]

    def pending(self, request_id):
        self._expire_pending()
        entry = self._pending.get(request_id or "")
        return entry[:2] if entry else None

    def complete(self, request_id, email, tenant):
        """Issue the authorization code for a signed-in user and return where to send the browser."""
        client_id, params, _ = self._pending.pop(request_id)
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
        return construct_redirect_uri(str(params.redirect_uri), code=code, state=params.state)

    async def load_authorization_code(self, client, authorization_code):
        found = self.store.code(authorization_code)
        if found is None:
            return None
        code = AuthorizationCode.model_validate(found[1])
        return code if code.client_id == client.client_id else None

    async def exchange_authorization_code(self, client, authorization_code):
        found = self.store.code(authorization_code.code)
        self.store.delete_code(authorization_code.code)
        if found is None:
            raise TokenError("invalid_grant", "authorization code expired or already used")
        return self._issue(
            client.client_id,
            found[0],
            authorization_code.subject,
            authorization_code.scopes,
            authorization_code.resource,
        )

    # Tokens

    def _issue(self, client_id, tenant, subject, scopes, resource):
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
        row = self.store.token(refresh_token, "refresh")
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
        row = self.store.token(refresh_token.token, "refresh")
        if row is None:
            raise TokenError("invalid_grant", "refresh token expired or revoked")
        if self.store.tenant(row["tenant"]) is None:
            raise TokenError("invalid_grant", "account no longer exists")
        self.store.delete_pair(refresh_token.token)
        return self._issue(client.client_id, row["tenant"], row["subject"], scopes or row["scopes"], row["resource"])

    async def load_access_token(self, token):
        row = self.store.token(token, "access")
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
        self.store.delete_pair(token.token)


PAGE = """<!doctype html><html lang="pt-BR"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Jurisync</title>
<body style="font-family:system-ui;max-width:26rem;margin:4rem auto;padding:0 1rem">
<h1>Entrar no Jurisync</h1>
<p>Um aplicativo pede acesso aos seus processos pelo Jurisync. Entre com o e-mail e a senha do seu escritório.</p>
{error}
<form method="post" action="/login">
<input type="hidden" name="request" value="{request}">
<p><label>E-mail<br><input name="email" type="email" required autofocus autocomplete="username"
 style="width:100%;padding:.6rem;font-size:1rem"></label></p>
<p><label>Senha<br><input name="password" type="password" required autocomplete="current-password"
 style="width:100%;padding:.6rem;font-size:1rem"></label></p>
<p><button style="padding:.6rem 1.2rem;font-size:1rem">Entrar e autorizar</button></p>
</form></body></html>"""

EXPIRED = HTMLResponse(
    "<!doctype html><meta charset='utf-8'><p style='font-family:system-ui;margin:4rem auto;max-width:26rem'>"
    "Este pedido de acesso expirou. Volte ao aplicativo e conecte de novo.</p>",
    status_code=400,
)


def login_routes(provider, throttle):
    def page(request_id, error=""):
        message = f"<p style='color:#b00020'>{html.escape(error)}</p>" if error else ""
        return HTMLResponse(
            PAGE.format(request=html.escape(request_id, quote=True), error=message),
            headers={"Cache-Control": "no-store", "X-Frame-Options": "DENY"},
        )

    async def show(request: Request):
        request_id = request.query_params.get("request", "")
        return page(request_id) if provider.pending(request_id) else EXPIRED

    async def submit(request: Request):
        form = await request.form()
        request_id = str(form.get("request", ""))
        if not provider.pending(request_id):
            return EXPIRED
        email = str(form.get("email", "")).strip().lower()
        if throttle.blocked(email):
            return page(request_id, "Muitas tentativas. Aguarde 15 minutos e tente de novo.")
        tenant = provider.store.authenticate(email, str(form.get("password", "")))
        if tenant is None:
            throttle.fail(email)
            return page(request_id, "E-mail ou senha incorretos.")
        throttle.clear(email)
        return RedirectResponse(provider.complete(request_id, email, tenant), status_code=302)

    return [Route("/login", show, methods=["GET"]), Route("/login", submit, methods=["POST"])]
