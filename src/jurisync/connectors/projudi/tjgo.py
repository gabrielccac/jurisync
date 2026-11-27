"""Projudi "tjgo-servlet" generation (TJGO): Java servlets, ISO-8859-1 HTML, AJAX JSON.

Login is the A1 certificate over mutual TLS: ``POST /LogOn`` (PaginaAtual=6) answers a redirect to
``/certificado?hashFluxo=...``, which asks the client certificate in the TLS handshake and redirects to ``/Usuario``;
the last hop (``LoginPronto=S``) is the authenticated panel. An account with several serventias (a lawyer enrolled in
several OAB sections) stops instead on ``/Usuario`` "Serventias Disponíveis", one ``Usuario?PaginaAtual=7&a1=...`` link
per serventia: ``TJGO_1G_USER_PROFILE`` picks one (see ``choose_serventia``). Later requests need only the session
cookie, and an expired session is redirected to ``/LogOn``.

A process is opened by CNJ number (``POST /BuscaProcesso``); its document index (``PaginaAtual=9``) exists only for the
process opened in the session, and a file downloads with its ``id_movi_arq`` and ``hash``. The search covers only active
processes unless told otherwise, so an archived one takes a second search with its own status code. The search form
keeps its filters in the session (the own-process lists leave a serventia type behind), so every search by number
starts with the form's "Limpar". TJGO's own lists
show a short number (``NNNNNNN-DD``), which the search also accepts.

Ciência (observed live 2026-11-17, ``tjgo_desk`` has the pages): a lawyer's TJGO intimações go out through the Diário
Eletrônico; each one is listed under "Intimações lidas" from the moment it is made available, with the publication
date as its reading date, which can still be in the future. The portal's own pending communications (awaiting reading)
are listed in the home page's "Pendências" block ("Não há pendências" when none). Before a document of a process is
read or saved, both lists are checked: a pending portal communication of that process, or a Diário intimação whose
publication date is still ahead, refuses the read unless the caller acknowledges it, and a list that cannot be read
in full refuses every read ("ciência not checked"). Only lists are read: no pendência is opened, marked or discarded.
"""

import re
import urllib.parse
from datetime import timedelta

from bs4 import BeautifulSoup
from curl_cffi import requests
from curl_cffi.requests.exceptions import ConnectionError as CurlConnectionError
from curl_cffi.requests.exceptions import RequestException, Timeout

from jurisync import portals
from jurisync.connectors.projudi import base, parsers, tjgo_desk
from jurisync.core.http import REDIRECTS, TIMEOUT, follow_redirects, impersonate_for, log
from jurisync.core.session_pool import LoginRejected

# TJGO login steps sometimes take longer than the default 30 s (17/11/2026: two tries timed out at 30 s).
LOGIN_TIMEOUT = 90
# TJGO answers on two IPv4 and two IPv6 addresses, and on 17/11/2026 one of them at a time dropped connections
# (curl: "Connection timed out after 90000 ms", or the TLS handshake closed). A short connect timeout lets the next
# attempt reach a live address instead of spending the whole read timeout on a dead one.
CONNECT_TIMEOUT = 15
CONNECT_ATTEMPTS = 3
# The desk lists (home page, "lidas" JSON, own processes) of a lawyer with many serventias can be slow.
LIST_TIMEOUT = 120


class PortalTimeout(RuntimeError):
    """TJGO did not answer in time; the message starts with ``portal_timeout``."""

    reason = "portal_unavailable"


def connect_failed(error):
    """Whether a request failed before it was sent (no connection or TLS handshake): repeating it is harmless."""
    text = str(error)
    if isinstance(error, Timeout):
        return "Connection timed out" in text or "Resolving timed out" in text
    return isinstance(error, CurlConnectionError) and ("SSL_connect" in text or "connect" in text.casefold())


def send(call, label, read_timeout):
    """Run ``call()`` (one request), repeating it when the connection itself failed; a timeout becomes
    ``PortalTimeout`` with a clear ``portal_timeout`` message."""
    for attempt in range(1, CONNECT_ATTEMPTS + 1):
        try:
            return call()
        except RequestException as error:
            if connect_failed(error) and attempt < CONNECT_ATTEMPTS:
                log.info("Projudi TJGO %s: connection failed (attempt %d), trying again", label, attempt)
                continue
            if isinstance(error, Timeout) or "timed out" in str(error):
                phase = "connect to" if connect_failed(error) else f"answer within {read_timeout} s for"
                raise PortalTimeout(
                    f"portal_timeout: Projudi TJGO did not {phase} the {label} ({attempt} attempt(s)); the portal "
                    "is slow or unreachable, try again later"
                ) from error
            raise


LOGIN_READY = "LoginPronto=S"
SESSION_COOKIE = "JSESSIONID"
XHR = {"X-Requested-With": "XMLHttpRequest", "Accept": "application/json, text/javascript, */*"}
# The panel holds a hidden password input (the signing dialog), so only the login form's own fields tell the two apart.
LOGIN_FIELDS = re.compile(r"""name\s*=\s*["']?(?:Senha|Usuario)["']?[\s>/]""", re.I)
NOT_FOUND = "Nenhum Processo foi localizado"
ARCHIVED = {"ProcessoStatus": "", "ProcessoStatusCodigo": "1"}
# A serventia choice is only ever followed with its ``a1`` (see ``require_safe_navigation``).
SERVENTIA_LINK = re.compile(r"^Usuario\?PaginaAtual=7&(?:[^#]*&)?a1=[^&#]+")
SERVENTIA_OAB = re.compile(r"(\d+)-[A-Z]-([A-Z]{2})\b")
SHORT_NUMBER = re.compile(r"(\d{1,7})[-.](\d{1,2})")
MAX_LIST_PAGES = 10


class UnsafeNavigation(RuntimeError):
    """A request this connector never sends: it ends the portal session."""


def require_safe_navigation(url):
    """Refuse the two requests that ended a live TJGO session (27/11/2026, a read-only exploration): the serventia
    switch ``Usuario?PaginaAtual=7`` without the ``a1`` that names the serventia (sent with ``LoginPronto=S``), and a
    bare ``/Usuario`` after it. Every other address passes unchanged."""
    parts = urllib.parse.urlsplit(url or "")
    if not parts.path.rstrip("/").endswith("/Usuario"):
        return url
    query = urllib.parse.parse_qs(parts.query, keep_blank_values=True)
    if not parts.query:
        raise UnsafeNavigation("Projudi TJGO: a bare /Usuario request ends the session; never sent")
    if "7" in query.get("PaginaAtual", []) and not any(query.get("a1", [])):
        raise UnsafeNavigation("Projudi TJGO: the serventia switch without its a1 ends the session; never sent")
    return url


def serventias(html):
    """``[(href, label)]`` of the "Serventias Disponíveis" page, e.g. ``("Usuario?PaginaAtual=7&a1=...",
    "Ordem dos Advogados do Brasil GO - GO / Advogados - OAB/Matrícula: 1234-N-GO")``; [] on any other page."""
    page = BeautifulSoup(html or "", "html.parser")
    found = []
    for fieldset in page.find_all("fieldset"):
        legend = fieldset.find("legend")
        for link in fieldset.find_all("a", href=SERVENTIA_LINK):
            label = " ".join(link.get_text(" ").split())
            group = " ".join(legend.get_text(" ").split()) if legend else ""
            found.append((link["href"].strip(), f"{group} / {label}" if group else label))
    return found


def _oab(value):
    """``GO22729`` from ``GO 22.729``, ``go-022729`` or ``22729GO``."""
    compact = re.sub(r"[^A-Z0-9]", "", (value or "").upper())
    swapped = re.fullmatch(r"(\d+)([A-Z]{2})", compact)
    if swapped:
        compact = swapped.group(2) + swapped.group(1)
    return re.sub(r"^([A-Z]{2})0+(?=[0-9])", r"\1", compact)


def choose_serventia(config, options):
    """The serventia link to follow (navigation only: the page has no "default" choice and none is ever set).

    ``<PREFIX>_USER_PROFILE`` names it by OAB (``GO22729``) or by a word of its label, as on the eproc; without it the
    single lawyer serventia is taken. No match or several stop the login with the choices instead of guessing.
    """
    wanted = config.env("USER_PROFILE").strip()
    if wanted:
        by_oab = [
            option
            for option in options
            if any(f"{uf}{int(n)}" == _oab(wanted) for n, uf in SERVENTIA_OAB.findall(option[1]))
        ]
        chosen = by_oab or [option for option in options if wanted.casefold() in option[1].casefold()]
    else:
        chosen = [option for option in options if "advogad" in option[1].casefold()]
    if len(chosen) != 1:
        names = "; ".join(label for _, label in options)
        raise LoginRejected(
            f"Projudi asks which serventia to use ({names}); set {config.env_name('USER_PROFILE')} to one of them "
            "(OAB such as GO12345, or a word of the label)",
            reason="unexpected",
        )
    return chosen[0][0]


def is_authenticated_panel(response):
    path = urllib.parse.urlsplit(response.url or "").path
    return (
        response.status_code == 200
        and path.rstrip("/").endswith("/Usuario")
        and not LOGIN_FIELDS.search(response.text or "")
    )


LOGIN_TIMEOUTS = (CONNECT_TIMEOUT, LOGIN_TIMEOUT)


def _follow(session, config, response, *, cert=None):
    # Each hop is a GET the portal asked for; repeating the walk after a failed connection repeats only those GETs.
    return send(
        lambda: follow_redirects(
            session,
            response,
            check=lambda url: base.require_portal_url(config, url, "Projudi login redirect"),
            cert=cert,
            impersonate=impersonate_for(config),
            timeout=LOGIN_TIMEOUTS,
        ),
        "login",
        LOGIN_TIMEOUT,
    )


def _login_get(session, config, url, **kwargs):
    return send(
        lambda: session.get(
            url, impersonate=impersonate_for(config), allow_redirects=False, timeout=LOGIN_TIMEOUTS, **kwargs
        ),
        "login",
        LOGIN_TIMEOUT,
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
        _follow(session, config, _login_get(session, config, config.url()))
        if not any(cookie.name == SESSION_COOKIE for cookie in session.cookies.jar):
            raise RuntimeError("Projudi did not start a session")
        # Only asks for the certificate flow (no credential is sent), so a failed connection may be tried again.
        started = send(
            lambda: session.post(
                config.url("LogOn"),
                data={"PaginaAtual": "6"},
                impersonate=impersonate_for(config),
                allow_redirects=False,
                timeout=LOGIN_TIMEOUTS,
            ),
            "login",
            LOGIN_TIMEOUT,
        )
        location = started.headers.get("Location") or ""
        if started.status_code not in REDIRECTS or "hashFluxo" not in location:
            raise RuntimeError("Projudi did not open the certificate login")
        url = urllib.parse.urljoin(started.url, location)
        base.require_portal_url(config, url, "Projudi login redirect")
        with base.client_certificate(pin=pin, p12_path=p12_path) as pem:
            response = _login_get(session, config, url, cert=pem)
            response = _follow(session, config, response, cert=pem)
        options = [] if LOGIN_READY in (response.url or "") else serventias(response.text)
        if options:
            url = urllib.parse.urljoin(response.url, choose_serventia(config, options))
            base.require_portal_url(config, url, "Projudi serventia")
            require_safe_navigation(url)
            response = _follow(session, config, _login_get(session, config, url))
            if not is_authenticated_panel(response) or serventias(response.text):
                raise RuntimeError("Projudi did not open the panel of the chosen serventia")
            return session
        if LOGIN_READY not in (response.url or "") or not is_authenticated_panel(response):
            raise LoginRejected("Projudi did not accept the A1 certificate", reason="certificate_refused")
        return session

    def refresh(self, portal, session):
        """The lawyer home answers only a live session. A bare ``/Usuario`` serves the public page with the login form
        even to a live session, and a dropped one gets a 200 "sessão expirada" page."""
        config = portals.projudi(portal)
        try:
            response = send(
                lambda: session.get(
                    config.url(tjgo_desk.HOME),
                    impersonate=impersonate_for(config),
                    allow_redirects=False,
                    timeout=(CONNECT_TIMEOUT, LIST_TIMEOUT),
                ),
                "home page",
                LIST_TIMEOUT,
            )
        except (RequestException, PortalTimeout):
            return None
        html = parsers.decode(response.content)
        alive = response.status_code == 200 and tjgo_desk.HOME_MARKER.search(html) and not LOGIN_FIELDS.search(html)
        return session if alive else None

    def normalize_number(self, value, portal):
        """A CNJ number, or the short ``NNNNNNN-DD`` (``NNNNNNN.DD``) that TJGO's own lists show."""
        text = (value or "").strip()
        short = SHORT_NUMBER.fullmatch(text)
        if short:
            return f"{int(short.group(1)):07d}-{int(short.group(2)):02d}"
        return base.normalize_number(text, portal)

    def request(self, session, portal, method, path, *, label="request", read_timeout=TIMEOUT, **kwargs):
        """The shared request, with a short connect timeout, a new attempt when the connection failed and a
        ``portal_timeout`` error when the portal does not answer. ``require_safe_navigation`` refuses the requests that
        end the session before anything is sent."""
        require_safe_navigation(portals.projudi(portal).url(path))
        kwargs.setdefault("timeout", (CONNECT_TIMEOUT, read_timeout))
        return send(
            lambda: super(TjgoAdapter, self).request(session, portal, method, path, **kwargs), label, read_timeout
        )

    def _page(self, response, label):
        """The decoded page of a 200 answer; a login form or "sessão expirada" means the session is gone."""
        if response.status_code != 200:
            raise RuntimeError(f"Projudi {label} failed (HTTP {response.status_code})")
        html = parsers.decode(response.content)
        if LOGIN_FIELDS.search(html) or tjgo_desk.is_expired(html):
            raise base.ProjudiSessionExpired("Projudi session expired")
        return html

    def _clear_search(self, session, portal):
        """The search form's own "Limpar" button (``PaginaAtual=4``): navigation only.

        ``BuscaProcesso`` keeps its filters in the session and a later request changes only the fields it sends. The
        own-process lists leave the last serventia type (``Id_ServentiaSubTipo``) and an archived search leaves its
        status behind, so a search by number after them looked only there: on 23/11/2026, after
        ``projudi_list_processes``, a process of another type answered "Nenhum Processo foi localizado" until the
        form was cleared.
        """
        self._page(self.request(session, portal, "POST", "BuscaProcesso", data={"PaginaAtual": "4"}), "search reset")

    def _search_process(self, session, portal, process_number, status=None):
        self._clear_search(session, portal)
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
        return self._page(response, "process search")

    def open_process(self, session, portal, process_number):
        html = self._search_process(session, portal, process_number)
        if not parsers.is_process_page(html) and NOT_FOUND in html:
            html = self._search_process(session, portal, process_number, ARCHIVED)
        if not parsers.is_process_page(html) and SHORT_NUMBER.fullmatch(process_number):
            try:
                rows, _ = tjgo_desk.parse_process_list(html)
            except base.ProjudiParseError:
                rows = []
            html = self._open_listed(session, portal, process_number, rows) or html
        if not parsers.is_process_page(html) and SHORT_NUMBER.fullmatch(process_number):
            # The search does not find every short number (17/11/2026: none of three 2nd-instance processes of a
            # lawyer); the lawyer's own lists carry each process' link, so a listed process opens through it.
            rows = [row for group in self._own_processes(session, portal)[0] for row in group["processes"]]
            html = self._open_listed(session, portal, process_number, rows) or html
        if not parsers.is_process_page(html):
            # A process the lawyer is intimated in without being linked to it (26/11/2026: an Ato Ordinatório in the
            # Diário; the search and the own lists did not have it) opens through its Diário row's link.
            try:
                rows = self._home(session, portal)["dje"]["items"]
            except base.ProjudiParseError:
                rows = []
            html = self._open_listed(session, portal, process_number, rows) or html
        if not parsers.is_process_page(html):
            raise ValueError("Process not found or not accessible to this account")
        process = parsers.parse_process(html)
        if SHORT_NUMBER.fullmatch(process_number):
            # A short number names sequence and check digits only; the page must carry the same pair.
            same = tjgo_desk.process_key(process["number"]) == tjgo_desk.process_key(process_number)
        else:
            same = re.sub(r"\D", "", process["number"]) == re.sub(r"\D", "", process_number)
        if not same:
            raise ValueError("Projudi opened a different process than the one requested")
        return process

    def _open_listed(self, session, portal, process_number, rows):
        """The page of the one listed row with the same sequence and check digits (a short number can match several
        processes, one per year or court: several stop the read), opened by the row's own link; ``None`` if none."""
        key = tjgo_desk.process_key(process_number)
        matching = {
            row.get("_id") or listed: row
            for row in rows
            if tjgo_desk.process_key(listed := row.get("number") or row["process"]) == key
        }
        if len(matching) > 1:
            raise ValueError(
                f"The short number {process_number} matches {len(matching)} processes; pass the full CNJ number"
            )
        row = next(iter(matching.values()), None)
        if row is None or not (row.get("_page") or row.get("_id")):
            return None
        if row.get("_page"):
            return row["_page"]  # a type with one process answered with its page already
        response = self.request(
            session, portal, "GET", f"BuscaProcesso?{urllib.parse.urlencode({'Id_Processo': row['_id']})}"
        )
        return self._page(response, "process page")

    # Lawyer desk -------------------------------------------------------------------------------------------------------

    def _home(self, session, portal):
        response = self.request(session, portal, "GET", tjgo_desk.HOME, label="home page", read_timeout=LIST_TIMEOUT)
        return tjgo_desk.parse_home(self._page(response, "home page"))

    def _awaiting_publication(self, session, portal):
        """Diário intimações and citações whose reading date is after today, from the "lidas" lists (they keep the
        ones the lawyer removed from the home page). ``(items, complete)``."""
        tomorrow = (tjgo_desk.today() + timedelta(days=1)).strftime("%d/%m/%Y")
        found = []
        for flow in tjgo_desk.READ_FLOWS:
            for position in range(MAX_LIST_PAGES):
                query = urllib.parse.urlencode(
                    {
                        "AJAX": "ajax",
                        "PaginaAtual": "3",
                        "fluxo": flow,
                        "dataInicialFim": tomorrow,
                        "dataFinalFim": "",
                        "nomeBusca": "",
                        "PosicaoPaginaAtual": str(position),
                    }
                )
                response = self.request(
                    session,
                    portal,
                    "GET",
                    f"PendenciaJson?{query}",
                    headers=XHR,
                    label="read-communications list",
                    read_timeout=LIST_TIMEOUT,
                )
                rows, awaiting = tjgo_desk.parse_read_page(self._page(response, "read communications"), flow)
                found.extend(awaiting)
                if len(rows) < tjgo_desk.PAGE_SIZE:
                    break
            else:
                return found, False
        return found, True

    def pending_ciencia(self, session, portal, process_number):
        """What a document read of this process could register: the portal's own pending communications (home page)
        and the Diário intimações not published yet. Only lists are read; no pendência is opened."""
        key = tjgo_desk.process_key(process_number)
        problems = []
        try:
            home = self._home(session, portal)
        except (base.ProjudiParseError, PortalTimeout) as error:
            home = {"portal": {"items": [], "complete": False}, "dje": {"items": []}}
            problems.append(
                str(error) if isinstance(error, PortalTimeout) else "the home page's Pendências could not be read"
            )
        if not home["portal"]["complete"] and not problems:
            problems.append("the home page lists pending communications in a layout this connector cannot read")
        try:
            upcoming, complete = self._awaiting_publication(session, portal)
            if not complete:
                problems.append("the Diário intimations awaiting publication could not be read in full")
        except PortalTimeout as error:
            upcoming = []
            problems.append(str(error))
        except base.ProjudiParseError:
            upcoming = []
            problems.append("the Diário intimations awaiting publication could not be read in full")
        seen, items = set(), []
        portal_items = [
            {**item, "kind": "pendência do portal (aguardando leitura)"} for item in home["portal"]["items"]
        ]
        for item in portal_items + upcoming + [i for i in home["dje"]["items"] if i["awaitingPublication"]]:
            if item["_key"] == key and (item.get("id"), item.get("text")) not in seen:
                seen.add((item.get("id"), item.get("text")))
                items.append(item)
        return {"items": items, "unchecked": "; ".join(problems) or None}

    def list_intimations(self, session, portal):
        home = self._home(session, portal)
        upcoming, upcoming_complete = self._awaiting_publication(session, portal)
        dje = home["dje"]
        shown = {item["id"] for item in dje["items"]}
        hidden = [item for item in upcoming if item["id"] not in shown]
        # No Diário table at all (nothing published) has no footer; otherwise the footer counts the rows.
        dje_complete = dje["declared"] == len(dje["items"]) or (dje["declared"] is None and not dje["items"])
        return {
            "diario": [tjgo_desk.public(item) for item in dje["items"]],
            "diarioCount": len(dje["items"]),
            "diarioDeclared": dje["declared"],
            "awaitingPublicationNotOnHome": [tjgo_desk.public(item) for item in hidden],
            "portalPending": [tjgo_desk.public(item) for item in home["portal"]["items"]],
            "portalPendingComplete": home["portal"]["complete"],
            "portalPendingText": home["portal"].get("text", ""),
            "complete": dje_complete and home["portal"]["complete"] and upcoming_complete,
        }

    def list_processes(self, session, portal):
        groups, complete, hearings = self._own_processes(session, portal)
        for group in groups:
            group["processes"] = [tjgo_desk.public(row) for row in group["processes"]]
        return {"categories": groups, "hearingsToday": hearings, "complete": complete}

    def _own_processes(self, session, portal):
        """``(groups, complete, hearings)``; rows keep ``_id`` (the portal's link to the process) or ``_page``."""
        home = self._home(session, portal)
        groups, complete = [], True
        for category in home["categories"]:
            query = urllib.parse.urlencode(
                {
                    "PaginaAtual": "2",
                    "Proprio": "1",
                    "Id_ServentiaSubTipo": category["subtype"],
                    "ProcessoStatusCodigo": "0",
                    "QuantidadeRegistrosPagina": "500",
                }
            )
            response = self.request(
                session, portal, "GET", f"BuscaProcesso?{query}", label="own-process list", read_timeout=LIST_TIMEOUT
            )
            html = self._page(response, "own-process list")
            if parsers.is_process_page(html):
                # A single result is opened at once (as a search by number does): its page stands for the row.
                process = parsers.parse_process(html)
                row = {"number": process["number"], "parties": process["parties"], "distributedAt": "", "_page": html}
                rows, total = [row], 1
            else:
                rows, total = tjgo_desk.parse_process_list(html)
                rows = self._more_processes(session, portal, rows, total)
            read_all = total is not None and total == len(rows)
            complete = complete and read_all
            groups.append({**category, "total": total, "complete": read_all, "processes": rows})
        return groups, complete, home["hearings"]

    def _more_processes(self, session, portal, rows, total):
        """Pages after the first (more than 500 processes of one type), through the list's own paging link."""
        position = 1
        while total is not None and len(rows) < total and position < MAX_LIST_PAGES:
            query = urllib.parse.urlencode(
                {"PaginaAtual": "2", "Paginacao": "true", "PosicaoPaginaAtual": str(position), "PassoBusca": "1"}
            )
            response = self.request(
                session, portal, "GET", f"BuscaProcesso?{query}", label="own-process list", read_timeout=LIST_TIMEOUT
            )
            more, _ = tjgo_desk.parse_process_list(self._page(response, "own-process list"))
            known = {row["number"] for row in rows}
            fresh = [row for row in more if row["number"] not in known]
            if not fresh:
                break
            rows = rows + fresh
            position += 1
        return rows

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
