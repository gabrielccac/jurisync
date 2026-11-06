"""Projudi "tjgo-servlet" generation (TJGO): Java servlets, ISO-8859-1 HTML, AJAX JSON.

Login is the A1 certificate over mutual TLS: ``POST /LogOn`` (PaginaAtual=6) answers a redirect to
``/certificado?hashFluxo=...``, which asks the client certificate in the TLS handshake and redirects to ``/Usuario``;
the last hop (``LoginPronto=S``) is the authenticated panel. Later requests need only the session cookie, and an
expired session is redirected to ``/LogOn``.

A process is opened by CNJ number (``POST /BuscaProcesso``); its document index (``PaginaAtual=9``) exists only for the
process opened in the session, and a file downloads with its ``id_movi_arq`` and ``hash``. The search covers only active
processes unless told otherwise, so an archived one takes a second search with its own status code.
"""

import re
import urllib.parse

from curl_cffi import requests
from curl_cffi.requests.exceptions import RequestException

from jurisync import portals
from jurisync.connectors.projudi import base, parsers
from jurisync.core.http import REDIRECTS, TIMEOUT, follow_redirects, impersonate_for

LOGIN_READY = "LoginPronto=S"
SESSION_COOKIE = "JSESSIONID"
XHR = {"X-Requested-With": "XMLHttpRequest", "Accept": "application/json, text/javascript, */*"}
# The panel holds a hidden password input (the signing dialog), so only the login form's own fields tell the two apart.
LOGIN_FIELDS = re.compile(r"""name\s*=\s*["']?(?:Senha|Usuario)["']?[\s>/]""", re.I)
NOT_FOUND = "Nenhum Processo foi localizado"
ARCHIVED = {"ProcessoStatus": "", "ProcessoStatusCodigo": "1"}


def is_authenticated_panel(response):
    path = urllib.parse.urlsplit(response.url or "").path
    return (
        response.status_code == 200
        and path.rstrip("/").endswith("/Usuario")
        and not LOGIN_FIELDS.search(response.text or "")
    )


def _follow(session, config, response, *, cert=None):
    return follow_redirects(
        session,
        response,
        check=lambda url: base.require_portal_url(config, url, "Projudi login redirect"),
        cert=cert,
        impersonate=impersonate_for(config),
    )


class TjgoAdapter(base.Adapter):
    flavor = "tjgo-servlet"

    def is_login_redirect(self, response):
        """``PaginaAtual=-200`` marks an expired session; any redirect to ``/LogOn`` means the same."""
        if response.status_code not in REDIRECTS:
            return False
        location = response.headers.get("Location") or ""
        return urllib.parse.urlsplit(location).path.rstrip("/").endswith("/LogOn")

    def login(self, portal, *, pin=None, p12_path=None, session=None):
        config = portals.projudi(portal)
        session = session or requests.Session()
        session.get(config.url(), impersonate=impersonate_for(config), timeout=TIMEOUT)
        if not any(cookie.name == SESSION_COOKIE for cookie in session.cookies.jar):
            raise RuntimeError("Projudi did not start a session")
        started = session.post(
            config.url("LogOn"),
            data={"PaginaAtual": "6"},
            impersonate=impersonate_for(config),
            allow_redirects=False,
            timeout=TIMEOUT,
        )
        location = started.headers.get("Location") or ""
        if started.status_code not in REDIRECTS or "hashFluxo" not in location:
            raise RuntimeError("Projudi did not open the certificate login")
        url = urllib.parse.urljoin(started.url, location)
        base.require_portal_url(config, url, "Projudi login redirect")
        with base.client_certificate(pin=pin, p12_path=p12_path) as pem:
            response = session.get(
                url, impersonate=impersonate_for(config), cert=pem, allow_redirects=False, timeout=TIMEOUT
            )
            response = _follow(session, config, response, cert=pem)
        if LOGIN_READY not in (response.url or "") or not is_authenticated_panel(response):
            raise RuntimeError("Projudi did not accept the A1 certificate")
        return session

    def refresh(self, portal, session):
        config = portals.projudi(portal)
        try:
            response = session.get(
                config.url("Usuario"),
                impersonate=impersonate_for(config),
                allow_redirects=False,
                timeout=TIMEOUT,
            )
        except RequestException:
            return None
        return session if response.status_code == 200 and not LOGIN_FIELDS.search(response.text) else None

    def _search_process(self, session, portal, process_number, status=None):
        response = self.request(
            session,
            portal,
            "POST",
            "BuscaProcesso",
            data={
                "PaginaAtual": "2",
                "ProcessoNumero": process_number,
                "BuscaPublica": "false",
                "QuantidadeRegistrosPagina": "10",
                **(status or {}),
            },
        )
        if response.status_code != 200:
            raise RuntimeError(f"Projudi process search failed (HTTP {response.status_code})")
        html = parsers.decode(response.content)
        if LOGIN_FIELDS.search(html):
            raise base.ProjudiSessionExpired("Projudi session expired")
        return html

    def open_process(self, session, portal, process_number):
        html = self._search_process(session, portal, process_number)
        if not parsers.is_process_page(html) and NOT_FOUND in html:
            html = self._search_process(session, portal, process_number, ARCHIVED)
        if not parsers.is_process_page(html):
            raise ValueError("Process not found or not accessible to this account")
        process = parsers.parse_process(html)
        if re.sub(r"\D", "", process["number"]) != re.sub(r"\D", "", process_number):
            raise ValueError("Projudi opened a different process than the one requested")
        return process

    def fetch_index(self, session, portal):
        response = self.request(
            session, portal, "GET", "BuscaProcesso?PaginaAtual=9&PassoBusca=5&AJAX=AJAX", headers=XHR
        )
        if response.status_code != 200:
            raise RuntimeError(f"Projudi document index failed (HTTP {response.status_code})")
        return parsers.parse_index(response.content)

    def movement_files(self, session, portal, movement_id):
        if not base.DOCUMENT_ID.fullmatch(movement_id or ""):
            raise ValueError("movement_id must be a numeric movement id from projudi_get_process")
        query = urllib.parse.urlencode({"AJAX": "ajax", "PaginaAtual": "8", "Id_Movimentacao": movement_id})
        response = self.request(session, portal, "GET", f"MovimentacaoArquivo?{query}", headers=XHR)
        if response.status_code != 200:
            raise RuntimeError(f"Projudi movement files failed (HTTP {response.status_code})")
        return parsers.parse_movement_files(response.content)

    def align_index(self, index, movements):
        return parsers.align_index(index, movements)

    def download_url(self, portal, document_id, hash_value):
        if not base.DOCUMENT_ID.fullmatch(document_id or "") or not base.DOCUMENT_HASH.fullmatch(hash_value or ""):
            raise ValueError("Document id or hash is not valid")
        query = urllib.parse.urlencode({"PaginaAtual": "6", "Id_MovimentacaoArquivo": document_id, "hash": hash_value})
        return portals.projudi(portal).url(f"BuscaProcesso?{query}")


ADAPTER = TjgoAdapter()
