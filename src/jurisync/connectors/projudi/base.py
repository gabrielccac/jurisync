"""Projudi plumbing shared by every generation of the system.

Projudi is one CNJ product in several front ends (TJGO's servlets, the older frameset of TJPR/TJAM/TJRR, TJPI's own).
What does not change lives here: the A1 certificate and its PEM, the HTTP session and its limits, the error model,
number and id validation, and the document reading that every tool shares. What changes per court (login flow,
search route, index and download routes, parsers) lives in an ``Adapter`` subclass chosen by ``Portal.flavor``.

Nothing here writes to Projudi. Reading a file can still count as the ciência of a pending intimação, so
``read_documents``/``download_document`` first ask the adapter's ``pending_ciencia`` and refuse, with nothing opened,
unless the caller acknowledges (``acknowledgeCiencia`` in the tools, as in the PJe server).
"""

import hashlib
import re
import urllib.parse
from contextlib import contextmanager

from curl_cffi.requests.exceptions import RequestException

from jurisync import portals
from jurisync.core import certificate, credentials
from jurisync.core.document_text import extract_document_text
from jurisync.core.documents import read_limited, validate_document_ids
from jurisync.core.documents_dir import save_document
from jurisync.core.http import REDIRECTS, TIMEOUT, impersonate_for, log_request, require_url
from jurisync.core.session_pool import SessionExpired

MAX_TEXT_CHARACTERS = 60_000
DOCUMENT_ID = re.compile(r"[0-9]{1,40}")
DOCUMENT_HASH = re.compile(r"[A-Za-z0-9]{1,128}")
DIGITS = re.compile(r"\d{20}")


class ProjudiSessionExpired(SessionExpired):
    """The portal sent the session back to its login page."""


class ProjudiParseError(RuntimeError):
    """The portal answered, but not with the page or JSON this connector reads."""


class CienciaPending(ValueError):
    """Opening a document of this process may register the ciência of a pending communication."""


class CienciaUnchecked(CienciaPending):
    """The pending communications could not be read in full, so whether a ciência is pending is unknown."""


def require_portal_url(config, value, label="Projudi URL"):
    return require_url(value, hosts={config.host}, label=label)


def require_download_url(config, value):
    """A download may hand over to the tribunal's own storage (a signed link on a sibling host, e.g. ``*.s3.tjgo.jus.br``).

    Only https hosts under the portal's parent domain are followed, never an arbitrary location.
    """
    domain = config.host.split(".", 1)[1]
    host = (urllib.parse.urlsplit(value or "").hostname or "").lower()
    allowed = {host} if host == config.host or host.endswith("." + domain) else set()
    return require_url(value, hosts=allowed, label="Projudi download")


# --- A1 certificate -------------------------------------------------------------------------------------------------


def _credential_names():
    """The A1 file and PIN names: the Projudi override when set, otherwise the shared PJe A1."""
    if credentials.has("PROJUDI_A1_P12"):
        return "PROJUDI_A1_P12", "PROJUDI_A1_PIN" if credentials.has("PROJUDI_A1_PIN") else "PJE_A1_PIN"
    return "PJE_A1_P12", "PJE_A1_PIN"


def load_certificate(*, pin=None, p12_path=None):
    """Load ``(key, cert, chain)`` from the Projudi override or the shared A1, honoring an explicit path."""
    p12_name, pin_name = _credential_names()
    pin = pin or credentials.env(pin_name)
    if not pin:
        raise RuntimeError(f"Set {pin_name} to the A1 certificate password")
    return certificate.load_a1(p12_path, pin, name=p12_name)


@contextmanager
def client_certificate(*, pin=None, p12_path=None):
    """The ``cert=`` pair for a handshake, valid only inside the block."""
    with certificate.client_certificate(*load_certificate(pin=pin, p12_path=p12_path)) as pem:
        yield pem


# --- Numbers and ids ------------------------------------------------------------------------------------------------


def normalize_number(value, portal):
    """A CNJ number with punctuation that belongs to this portal's court."""
    config = portals.projudi(portal)
    text = (value or "").strip()
    if DIGITS.fullmatch(text):
        text = f"{text[:7]}-{text[7:9]}.{text[9:13]}.{text[13]}.{text[14:16]}.{text[16:]}"
    match = portals.CNJ.match(text)
    if not match or (match.group(1), match.group(2)) != (config.justice, config.region):
        raise ValueError(f"process_number must be a CNJ number of {config.tribunal} ({config.key})")
    return text


def public_file(file):
    """A file as the agent sees it: never the hash that authorizes its download."""
    return {key: value for key, value in file.items() if key != "hash"}


def file_format(content, content_type):
    if content.lstrip().startswith(b"%PDF-") or "pdf" in content_type.casefold():
        return "pdf"
    return "html" if "html" in content_type.casefold() else "other"


# --- Adapter --------------------------------------------------------------------------------------------------------


class Adapter:
    """One Projudi generation: how to log in, open a process, list its files and download one.

    Methods raise ``ProjudiSessionExpired`` when the portal sends the session to its login page and
    ``ProjudiParseError`` when a page does not have the expected structure.
    """

    flavor = ""

    def login(self, portal):
        raise NotImplementedError

    def refresh(self, portal, session):
        """Cheap liveness check for the session pool: the session, or ``None`` to ask for a new login."""
        raise NotImplementedError

    def is_login_redirect(self, response):
        raise NotImplementedError

    def normalize_number(self, value, portal):
        return normalize_number(value, portal)

    def pending_ciencia(self, session, portal, process_number):
        """Communications of this process whose ciência a document read could register, checked before anything is
        opened: ``{"items": [...], "unchecked": reason or None}``, or ``None`` when the generation has no such list
        (TJAM checks its agenda inside ``download``)."""
        return None

    def list_intimations(self, session, portal):
        raise NotImplementedError(
            f"Projudi {portals.projudi(portal).tribunal} has no intimation list in this connector"
        )

    def list_processes(self, session, portal):
        raise NotImplementedError(f"Projudi {portals.projudi(portal).tribunal} has no process list in this connector")

    def open_process(self, session, portal, process_number):
        """Open one process by normalized CNJ number; return ``{"number", ..., "movements": [...]}``."""
        raise NotImplementedError

    def fetch_index(self, session, portal):
        """The index of the opened process: ``[{"title", "complement", "files": [{"id", "name", "hash"}]}]``."""
        raise NotImplementedError

    def movement_files(self, session, portal, movement_id):
        raise NotImplementedError

    def align_index(self, index, movements):
        """Index entries joined to the page's movements, or ``None`` when they cannot be paired safely."""
        return None

    def download_url(self, portal, document_id, hash_value):
        raise NotImplementedError

    # Shared request plumbing -----------------------------------------------------------------------------------------

    def request(self, session, portal, method, path, **kwargs):
        config = portals.projudi(portal)
        url = config.url(path)
        require_portal_url(config, url)
        kwargs.setdefault("timeout", TIMEOUT)
        response = session.request(method, url, impersonate=impersonate_for(config), allow_redirects=False, **kwargs)
        log_request(method, url, response.status_code)
        if self.is_login_redirect(response):
            raise ProjudiSessionExpired("Projudi session expired")
        if response.status_code in REDIRECTS:
            raise RuntimeError(f"Projudi redirected unexpectedly (HTTP {response.status_code})")
        return response

    def download(self, session, portal, document_id, hash_value):
        """``(content, content_type)`` of one file, or ``(None, reason)`` when it is too large or not served."""
        config = portals.projudi(portal)
        url = self.download_url(portal, document_id, hash_value)
        require_portal_url(config, url)
        response = session.request(
            "GET", url, impersonate=impersonate_for(config), allow_redirects=False, timeout=TIMEOUT, stream=True
        )
        try:
            for hop in range(3):  # the portal may answer with a redirect to its signed storage link
                # A storage path carries the file name, so after the first hop only the host is logged.
                log_request(
                    "GET", url if hop == 0 else f"https://{urllib.parse.urlsplit(url).hostname}/", response.status_code
                )
                if self.is_login_redirect(response):
                    raise ProjudiSessionExpired("Projudi session expired")
                location = response.headers.get("Location")
                if response.status_code not in REDIRECTS or not location:
                    break
                response.close()
                url = urllib.parse.urljoin(url, location)
                require_download_url(config, url)
                response = session.request(
                    "GET", url, impersonate=impersonate_for(config), allow_redirects=False, timeout=TIMEOUT, stream=True
                )
            if response.status_code != 200:
                return None, f"http_{response.status_code}"
            content = read_limited(response)
            if content is None:
                return None, "too_large"
            return content, response.headers.get("Content-Type", "")
        finally:
            response.close()

    def close(self, session):
        session.close()


# --- Reads shared by every flavor -----------------------------------------------------------------------------------


def load_process(adapter, session, portal, process_number, *, documents=True):
    """``(process, index)``: the process page and, with ``documents``, its index joined to the movements."""
    process = adapter.open_process(session, portal, adapter.normalize_number(process_number, portal))
    if not documents:
        return process, []
    index = adapter.fetch_index(session, portal)
    if not index:
        # The index is empty without a process in context; a process with movements always has an index.
        raise ProjudiParseError("Projudi returned an empty document index for a process with movements")
    aligned = adapter.align_index(index, process["movements"])
    if aligned is not None:
        process["movements"] = aligned
    return process, index


def index_files(index):
    """Every file of the index by id."""
    return {file["id"]: {**file, "movement": entry["title"]} for entry in index for file in entry["files"]}


CIENCIA_NOTICE = "Ler este conteúdo pode ter registrado a ciência da parte e iniciado o prazo; informe o usuário."


def _describe(item):
    parts = [item.get("id") or "", item.get("kind") or "intimação", item.get("process") or ""]
    if item.get("publication"):
        parts.append(f"publicação/leitura {item['publication']}")
    if item.get("deadline"):
        parts.append(f"prazo {item['deadline']}")
    return " ".join(part for part in parts if part)


def check_ciencia(adapter, session, portal, process_number, acknowledge=False):
    """Run the generation's pending-ciência check before anything of the process is opened.

    Refuses (``CienciaPending``/``CienciaUnchecked``) unless the caller acknowledges; with the acknowledgement it
    returns what the read may have done, for the result.
    """
    state = adapter.pending_ciencia(session, portal, adapter.normalize_number(process_number, portal))
    if state is None:
        return {}
    items = [{k: v for k, v in item.items() if not k.startswith("_")} for item in state.get("items") or []]
    unchecked = state.get("unchecked")
    if not acknowledge:
        if unchecked:
            raise CienciaUnchecked(
                f"ciência not checked: {unchecked}, so it is unknown whether this process has a communication whose "
                "ciência is still pending. Nothing was opened. Try again later (projudi_list_intimations shows the "
                "list when it can be read). Opening it anyway may register the party's ciência and start the "
                "deadline: only with the user's explicit permission, repeat the call with acknowledgeCiencia=true."
            )
        if items:
            shown = "; ".join(_describe(item) for item in items[:5])
            raise CienciaPending(
                f"ciência not yet registered for: {shown}. Nothing was opened. Opening a document of this process "
                "may register the party's ciência and start the deadline. Ask the user for explicit permission; if "
                "they agree, repeat the call with acknowledgeCiencia=true."
            )
    extra = {}
    if unchecked:
        extra["cienciaChecked"] = False
    if items or unchecked:
        extra["ciencia"] = {"pendenteAoLer": items, "aviso": CIENCIA_NOTICE}
    return extra


def read_documents(adapter, session, portal, process_number, document_ids, *, acknowledge_ciencia=False):
    """``(number, documents, ciencia)``: text of 1-5 documents of a process; ids come from this process' fresh
    index. The pending-ciência check runs before the process is opened."""
    validate_document_ids(document_ids)
    ciencia = check_ciencia(adapter, session, portal, process_number, acknowledge_ciencia)
    process, index = load_process(adapter, session, portal, process_number)
    known = index_files(index)
    if any(identifier not in known for identifier in document_ids):
        raise ValueError("Selected document ID is not in this process")
    results = []
    for identifier in document_ids:
        file = known[identifier]
        result = {
            "id": identifier,
            "name": file["name"],
            "movement": file["movement"],
            "extractionStatus": "request_failed",
        }
        results.append(result)
        try:
            content, content_type = adapter.download(session, portal, identifier, file["hash"])
        except RequestException:
            continue
        if content is None:
            result["extractionStatus"] = content_type
            continue
        status, text = extract_document_text(content, content_type, process["number"], allow_unnumbered=True)
        result.update(
            {
                "extractionStatus": status,
                "format": file_format(content, content_type),
                "bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
                "text": text[:MAX_TEXT_CHARACTERS],
                "textTruncated": len(text) > MAX_TEXT_CHARACTERS,
            }
        )
    return process["number"], results, ciencia


def download_document(adapter, session, portal, process_number, document_id, *, acknowledge_ciencia=False):
    """Save one document in the shared documents folder and describe it. Never returns the hash or a URL."""
    validate_document_ids([document_id])
    ciencia = check_ciencia(adapter, session, portal, process_number, acknowledge_ciencia)
    process, index = load_process(adapter, session, portal, process_number)
    file = index_files(index).get(document_id)
    if file is None:
        raise ValueError("Selected document ID is not in this process")
    content, content_type = adapter.download(session, portal, document_id, file["hash"])
    if content is None:
        return {"id": document_id, "name": file["name"], "saved": False, "reason": content_type, **ciencia}
    kind = file_format(content, content_type)
    status, _ = extract_document_text(content, content_type, process["number"], allow_unnumbered=True)
    result = {
        "id": document_id,
        "name": file["name"],
        "movement": file["movement"],
        "format": kind,
        "bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
        "extractionStatus": status,
        **ciencia,
    }
    if status in ("access_denied", "session_expired"):
        return {**result, "saved": False}
    extension = {"pdf": "pdf", "html": "html"}.get(kind, "bin")
    digits = re.sub(r"\D", "", process["number"])
    result["savedAs"] = save_document(f"{digits}_{document_id}.{extension}", content)
    result["saved"] = True
    return result
