"""Projudi "legacy-local" generation (TJAM): a local login form in front of the Projudi lawyer desk.

Validated live on 2026-11-16 (one login, a lawyer account): login, desk, process search, process page, parties,
movement files and one PDF download. Routes and shapes observed then:

- Login: ``GET usuario/logon.do?actionType=inicio`` returns ``form#formLogin`` (``login``, ``senha``, hidden ``tck``,
  ``latitude``, ``longitude``, ``locationGranted``). Its ``submitLogin()`` sets ``tck = md5(<per-render salt> + login)``
  and disables the submit button, so a browser sends no button. The POST redirects to ``usuario/postLogon.do``, a
  frameset whose script builds the menu with ``add_menu("<label>", "/<path>?_tj=<token>", ...)`` calls. There is no
  A1/mTLS login and no second factor.
- Session check: the lawyer desk ``usuario/mesaAdvogado.do?actionType=listaInicio`` (logout link present). A session
  the portal dropped answers 200 with "A sessao expirou" on every page.
- Search: menu "Processos 1º Grau" opens ``form[name=buscaProcessosQualquerInstanciaForm]``; the number goes in
  ``numeroProcesso`` as digits only (the formatted number is ignored and the page asks for a party name).
- Process: the result row links to ``processo.do?_tj=...``. Movements are ``tr[id^=mov]`` rows (expand icon, sequence,
  date and time, event, author). A movement's files load with a POST of ``tipoLayout=1`` to the
  ``movimentacaoArquivoDocumento.do?_tj=...`` address the page declares for it; each file links to ``arquivo.do?_tj=``.
  Parties are the ``tabPartes`` tab of ``processoForm`` (only names, roles and lawyers are kept: no CPF or address).

Safety rules (the generic read rules are in ``legacy.py``):
- one credential POST per ``login``; no retry (a wrong password counts toward a lock) and ``SessionPool`` blocks
  further logins after ``LoginRejected``;
- a WAF/CAPTCHA page (F5 "Request Rejected") stops the login (``HumanVerificationRequired``), never bypassed;
- a document is read only when the desk's intimação agenda ("Aguardando Ciência") has no entry for that process:
  otherwise ``DocumentsBlocked``, because opening a file there could count as reading the intimação;
- the connector never asks for or uses the internal Projudi certificate password.
"""

import hashlib
import re
import urllib.parse
import weakref

from curl_cffi import requests
from curl_cffi.requests.exceptions import RequestException

from jurisync import portals
from jurisync.connectors.projudi import base, legacy, parsers
from jurisync.connectors.projudi.legacy import HumanVerificationRequired, MissingCredentials
from jurisync.core.http import REDIRECTS, TIMEOUT, follow_redirects, impersonate_for, log_request, require_url
from jurisync.core.session_pool import LoginRejected

LOGIN_PATH = "usuario/logon.do"
USERNAME_PATTERN = re.compile(r"[A-Za-z0-9]{3,30}")
SALT = re.compile(r'hex_md5\(\s*"([0-9a-fA-F]{16,128})"\s*\+\s*login\s*\)')
REJECTED = ("usuário ou senha inválidos", "usuario ou senha invalidos", "bloquead")
ERROR_PAGE = "erro de acesso ao projudi"
POST_LOGON = "usuario/postLogon.do?actionType=iniciar"
DESK = "usuario/mesaAdvogado.do?actionType=listaInicio"
MENU_ITEM = re.compile(r'add_menu\(\s*"([^"]*)"\s*,\s*"(/[^"]+)"')
SEARCH_MENU = "Processos 1º Grau"
SEARCH_FORM = "buscaProcessosQualquerInstanciaForm"
FILES_SCRIPT = re.compile(
    r'"(/projudi/processo/movimentacaoArquivoDocumento\.do\?_tj=[0-9a-fA-F]+)",\s*\{\s*sourceClass:\s*"([\w-]+)"'
)
TOKEN = re.compile(r"[0-9a-fA-F]{16,512}")
FILE_LINK = re.compile(r"arquivo\.do\?_tj=([0-9a-fA-F]{16,512})$")
FILE_LABEL = re.compile(r"^(\d+)\.(\d+)\s*Arquivo:\s*(.*)$", re.S)
RECORDS = re.compile(r"(\d+)\s+registro\(s\) encontrado\(s\)")
DOCUMENT_ID = re.compile(r"(\d{1,6})(\d{3})")
# Per-session navigation state (menu tokens, the process last opened): Projudi keeps the "current process" on its side.
_state = weakref.WeakKeyDictionary()


def login_credentials(config):
    """``(login, password)``: a ``UF000`` login or a CPF/CNPJ. Raises before any request when one is missing."""
    username = "".join(config.env("USERNAME").split())
    if re.fullmatch(r"[\d.\-/]+", username):
        username = re.sub(r"\D", "", username)
    password = config.env("PASSWORD")
    missing = [config.env_name(name) for name, value in (("USERNAME", username), ("PASSWORD", password)) if not value]
    if missing:
        raise MissingCredentials(
            f"precisa de login e senha da cliente: set {' and '.join(missing)} (Projudi {config.tribunal})"
        )
    if not USERNAME_PATTERN.fullmatch(username) or len(password) > 30:
        raise ValueError(f"{config.env_name('USERNAME')} must be a UF000 login or a CPF/CNPJ; password up to 30 chars")
    return username, password


def login_key(salt, username):
    """The ``tck`` field, as the page's ``hex_md5(salt + login)`` computes it (a standard MD5 over 8-bit characters)."""
    return hashlib.md5((salt + username).encode("latin-1"), usedforsecurity=False).hexdigest()


def is_logged_in_page(html):
    """An authenticated Projudi page: no login form and a logout link."""
    return "formlogin" not in html.casefold() and "actionType=logout" in html


def menu_links(html):
    """``{label: path}`` of the frameset's ``add_menu`` script calls (paths start at the application root)."""
    links = {}
    for label, path in MENU_ITEM.findall(html or ""):
        links.setdefault(" ".join(label.split()), path)
    return links


def clean(node):
    return " ".join((node.get_text(" ") if hasattr(node, "get_text") else str(node or "")).split())


def document_id(sequence, item):
    """A numeric id for file ``<sequence>.<item>``: the sequence followed by the item in three digits."""
    if not (sequence.isdigit() and item.isdigit() and 0 < int(item) < 1000):
        raise base.ProjudiParseError("Projudi file label has an unexpected shape")
    return f"{int(sequence)}{int(item):03d}"


def parse_process_page(html, number):
    """Case fields and movements of a TJAM ``processo.do`` page, with each movement's file-list address."""
    page = legacy.soup(html)
    if number not in clean(page):
        raise ValueError("Process not found or not accessible to this account")
    fields = {}
    for label in page.select("td.label"):
        value = label.find_next_sibling("td")
        name = clean(label).rstrip(":")
        if value is not None and name and name not in fields and len(fields) < 20:
            fields[name] = clean(value)
        if name == "Nível de Sigilo":
            break
    scripts = {source: path for path, source in FILES_SCRIPT.findall(html)}
    movements, files = [], {}
    for row in page.select('tr[id^="mov"]'):
        cells = row.find_all("td", recursive=False)
        if len(cells) < 5:
            continue
        sequence = clean(cells[1])
        moment = clean(cells[2]).split()
        anchor = row.find("a", class_=re.compile(r"^linkArquivosmovimentacoes\d+$"))
        source = next((c for c in (anchor.get("class") if anchor else []) if c.startswith("linkArquivos")), None)
        if sequence.isdigit() and source in scripts:
            files[sequence] = scripts[source]
        movements.append(
            {
                "sequence": sequence,
                "movementId": sequence if sequence.isdigit() else "",
                "description": clean(cells[3]),
                "date": moment[0] if moment else "",
                "time": moment[1] if len(moment) > 1 else "",
                "user": clean(cells[4]),
                "hasFiles": sequence in files,
            }
        )
    if not movements and "registro(s) encontrado(s)" not in clean(page):
        raise base.ProjudiParseError("TJAM process page has no movements table")
    return {"number": number, "fields": fields, "movements": movements}, files


def parse_parties(html):
    """``[{role, name, lawyers}]`` of the ``tabPartes`` tab. Documents, RG and addresses are left out."""
    tab = legacy.soup(html).find(id="tabprefix2")
    parties = []
    for heading in tab.find_all("h4") if tab is not None else []:
        table = heading.find_next_sibling("table")
        if table is None or "resultTable" not in (table.get("class") or []):
            continue
        for row in table.select("tbody > tr"):
            cells = row.find_all("td", recursive=False)
            if len(cells) >= 6 and row.find("img", id=re.compile(r"^icon")):
                lawyers = clean(cells[5])
                parties.append(
                    {
                        "role": clean(heading),
                        "name": clean(cells[1]),
                        "lawyers": "" if lawyers == "Parte sem advogado" else lawyers,
                    }
                )
    return parties


def parse_movement_files(html, sequence):
    """Files of one movement: ``[{id, label, description, signer, name, hash}]`` (``hash`` is the download token)."""
    files = []
    for row in legacy.soup(html).find_all("tr"):
        cells = row.find_all("td", recursive=False)
        link = row.find("a", href=re.compile(r"arquivo\.do\?_tj="))
        if not cells or link is None:
            continue
        label = FILE_LABEL.match(clean(cells[0]))
        token = FILE_LINK.search(legacy.strip_path_params(link["href"]))
        if label is None or token is None or label.group(1) != sequence:
            raise base.ProjudiParseError("TJAM movement file row has an unexpected shape")
        signer = next((clean(c).removeprefix("Ass.:").strip() for c in cells if clean(c).startswith("Ass.:")), "")
        files.append(
            {
                "id": document_id(label.group(1), label.group(2)),
                "label": f"{label.group(1)}.{label.group(2)}",
                "description": label.group(3).strip(),
                "signer": signer,
                "name": clean(link),
                "hash": token.group(1),
            }
        )
    return files


def pending_intimations(html):
    """Process numbers in the desk's intimação agenda, and whether the page lists every entry it counts."""
    tab = legacy.soup(html).find(id="tabprefix1") or legacy.soup(html)
    text = clean(tab)
    numbers = set(re.findall(r"\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}", text))
    counted = RECORDS.search(text)
    if counted is None:
        raise base.ProjudiParseError("TJAM intimação agenda has an unexpected shape")
    return numbers, int(counted.group(1)) <= len(numbers) or int(counted.group(1)) == 0


class TjamAdapter(legacy.LegacyAdapter):
    flavor = "legacy-local"

    def _follow(self, session, config, response):
        return follow_redirects(
            session,
            response,
            check=lambda url: require_url(url, hosts={config.host}, label="Projudi login redirect"),
            rewrite=legacy.strip_path_params,
            impersonate=impersonate_for(config),
        )

    def _state(self, session):
        try:
            return _state.setdefault(session, {})
        except TypeError:  # an object without weak references (test doubles): no cached navigation
            return {}

    def login(self, portal, *, session=None):
        config = portals.projudi(portal)
        username, password = login_credentials(config)  # nothing is requested before the credentials exist
        session = session or requests.Session()
        first = session.get(
            config.url(f"{LOGIN_PATH}?actionType=inicio"),
            impersonate=impersonate_for(config),
            allow_redirects=False,
            timeout=TIMEOUT,
        )
        page = self._follow(session, config, first)
        html = parsers.decode(page.content)
        if legacy.has_human_verification(html):
            raise HumanVerificationRequired("precisa de uma pessoa: o TJAM bloqueou a página (WAF/CAPTCHA)")
        form = legacy.soup(html).find("form", id="formLogin")
        salt = SALT.search(html)
        if page.status_code != 200 or form is None or salt is None:
            raise base.ProjudiParseError("TJAM login page has no login form or salt")
        # The action keeps ``;jsessionid`` and ``_tj`` exactly as the page wrote them; only its target is validated.
        action = urllib.parse.urljoin(page.url, form.get("action") or "")
        require_url(
            legacy.strip_path_params(action),
            hosts={config.host},
            path=f"{config.root}{LOGIN_PATH}",
            label="Projudi login form",
        )
        data = {
            **legacy.hidden_fields(form),
            "login": username,
            "senha": password,
            "tck": login_key(salt.group(1), username),
            "locationGranted": "false",
        }
        response = session.post(
            action, data=data, impersonate=impersonate_for(config), allow_redirects=False, timeout=TIMEOUT
        )
        log_request("POST", legacy.strip_path_params(action), response.status_code)
        result = self._follow(session, config, response) if response.status_code in REDIRECTS else response
        text = parsers.decode(result.content)
        lowered = text.casefold()
        if legacy.has_human_verification(text):
            raise HumanVerificationRequired("precisa de uma pessoa: o TJAM pediu verificação (WAF/CAPTCHA)")
        if any(marker in lowered for marker in REJECTED):
            raise LoginRejected("Projudi did not accept the login and password")
        if ERROR_PAGE in lowered:
            raise RuntimeError("Projudi answered a generic access error to the login")
        if self.refresh(portal, session) is None:
            raise RuntimeError("Projudi login did not reach an authenticated page")
        menu = menu_links(text)
        if menu:
            self._state(session)["menu"] = menu
        return session

    def refresh(self, portal, session):
        config = portals.projudi(portal)
        try:
            response = session.get(
                config.url(DESK), impersonate=impersonate_for(config), allow_redirects=False, timeout=TIMEOUT
            )
        except RequestException:
            return None
        if response.status_code != 200:
            return None
        return session if is_logged_in_page(parsers.decode(response.content)) else None

    # Reads ------------------------------------------------------------------------------------------------------------

    def _menu(self, session, portal):
        state = self._state(session)
        if not state.get("menu"):
            _url, html = self._get_page(session, portal, portals.projudi(portal).url(POST_LOGON))
            state["menu"] = menu_links(html)
        return state["menu"]

    def open_process(self, session, portal, process_number):
        config = portals.projudi(portal)
        path = self._menu(session, portal).get(SEARCH_MENU)
        if not path:
            raise base.ProjudiParseError(f"Projudi menu has no '{SEARCH_MENU}' search for this session")
        url, html = self._get_page(session, portal, config.url(path))
        form = legacy.soup(html).find("form", attrs={"name": SEARCH_FORM})
        if form is None:
            raise base.ProjudiParseError("TJAM search page has no process search form")
        action = legacy.strip_path_params(urllib.parse.urljoin(url, form.get("action") or ""))
        base.require_portal_url(config, action)
        fields = {
            **legacy.hidden_fields(form),
            "filtroAdvogado": "qualquerAdvogado",
            "numeroProcesso": re.sub(r"\D", "", process_number),
            "flagNumeroProcessoUnico": "true",
        }
        response = self.request(session, portal, "POST", action[len(config.base_url) :], data=fields)
        if response.status_code != 200:
            raise RuntimeError(f"Projudi process search failed (HTTP {response.status_code})")
        result = parsers.decode(response.content)
        link = next(
            (
                link
                for text, link in legacy.page_links(result, response.url or action)
                if text == process_number and urllib.parse.urlsplit(link).path == f"{config.root}processo.do"
            ),
            None,
        )
        if link is None:
            raise ValueError("Process not found or not accessible to this account")
        page_url, page = self._get_page(session, portal, link)
        process, files = parse_process_page(page, process_number)
        process["parties"] = self._parties(session, portal, page_url, page)
        self._state(session)["process"] = {"number": process_number, "files": files}
        return process

    def _parties(self, session, portal, page_url, page):
        """The parties tab: the process form posted with ``selectedIcon=tabPartes``. Best effort: [] when it differs."""
        config = portals.projudi(portal)
        form = legacy.soup(page).find("form", id="processoForm")
        if form is None:
            return []
        action = legacy.strip_path_params(urllib.parse.urljoin(page_url, form.get("action") or ""))
        base.require_portal_url(config, action)
        fields = {**legacy.hidden_fields(form), "selectedIcon": "tabPartes"}
        response = self.request(session, portal, "POST", action[len(config.base_url) :], data=fields)
        return parse_parties(parsers.decode(response.content)) if response.status_code == 200 else []

    def _process_files(self, session):
        process = self._state(session).get("process")
        if not process:
            raise base.ProjudiParseError("No TJAM process is open in this session")
        return process

    def movement_files(self, session, portal, movement_id):
        path = self._process_files(session)["files"].get(str(movement_id))
        if path is None:
            return []
        response = self.request(
            session, portal, "POST", path.removeprefix(portals.projudi(portal).root), data={"tipoLayout": "1"}
        )
        if response.status_code != 200:
            raise RuntimeError(f"Projudi movement files failed (HTTP {response.status_code})")
        return parse_movement_files(parsers.decode(response.content), str(movement_id))

    def fetch_index(self, session, portal):
        """One entry per movement with files, newest first, as the page lists them (one POST per movement)."""
        files = self._process_files(session)["files"]
        return [
            {
                "title": sequence,
                "complement": "",
                "movementId": sequence,
                "files": self.movement_files(session, portal, sequence),
            }
            for sequence in files
        ]

    def align_index(self, index, movements):
        by_movement = {entry["movementId"]: entry["files"] for entry in index}
        return [{**movement, "files": by_movement.get(movement["movementId"], [])} for movement in movements]

    def download_url(self, portal, document_id, hash_value):
        if not TOKEN.fullmatch(hash_value or ""):
            raise ValueError("Invalid Projudi file token")
        return portals.projudi(portal).url(f"arquivo.do?_tj={hash_value}")

    def download(self, session, portal, document_id, hash_value):
        """A file, only when the desk's intimação agenda lists nothing pending for the process that is open."""
        number = self._process_files(session)["number"]
        response = self.request(session, portal, "POST", DESK, data={"selectedIcon": "tabIntimacoes"})
        numbers, complete = pending_intimations(parsers.decode(response.content))
        if number in numbers or not complete:
            raise legacy.DocumentsBlocked(
                "This process has an intimação awaiting ciência for this account (or the agenda could not be read in "
                "full): opening its files could count as reading it. Open it in the portal if the user decides to."
            )
        content, kind = base.Adapter.download(self, session, portal, document_id, hash_value)
        if (
            content is not None
            and not content.lstrip().startswith(b"%PDF-")
            and legacy.session_expired(parsers.decode(content))
        ):
            raise base.ProjudiSessionExpired("Projudi session expired")
        return content, kind


ADAPTER = TjamAdapter()
