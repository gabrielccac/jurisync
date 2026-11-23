"""PJe-JT 2.x (PJe-KZ): the lawyer panel and process pages as REST, for portals with ``flavor="kz"``.

TRT/TST portals no longer serve the JSF lawyer panel (``advogado.seam`` redirects to the Angular app under
``/pjekz/``) nor the ``fPP`` process search. The app reads everything from ``/pje-comum-api/api`` with the cookies
the login set (``access_token`` renewed by ``/pje-seguranca/api/token/refresh``). This module speaks that API and
returns the same result shapes as the JSF connector (counters, inbox rows, process dict, document reads).

Everything here is GET: the only ciência endpoint of the API is a PUT that this module never calls, and a document
that belongs to an expediente without ciência is still refused by the callers' ``pending_notice`` check. That check
needs every expediente of the process, so ``read_expedientes`` reads all pages and says when it could not.
"""

import base64
import copy
import hashlib
import json
import re
import threading
import time
import weakref
from datetime import datetime

from curl_cffi.requests.exceptions import RequestException

from jurisync import portals
from jurisync.connectors.pje.parsers import parse_process_number
from jurisync.core.document_text import extract_document_text
from jurisync.core.documents import MAX_DOCUMENT_BYTES, validate_document_ids
from jurisync.core.documents_dir import save_document
from jurisync.core.http import IMPERSONATE, TIMEOUT, log_request
from jurisync.core.session_pool import SessionExpired

API = "/pje-comum-api/api"
TOKEN = "/pje-seguranca/api/token/refresh"
MEUS_EXPEDIENTES = 2
PAGE_SIZE = 40
MAX_EXPEDIENTE_PAGES = 50
MAX_TIMELINE = 1000
FIRST_PAGE_EVENTS = 50
MONTHS = ("jan.", "fev.", "mar.", "abr.", "mai.", "jun.", "jul.", "ago.", "set.", "out.", "nov.", "dez.")
FILTERS = {"processNumber", "from", "to", "expedienteId", "classe"}


class Api:
    """GET-only client of one portal's REST API on an authenticated session."""

    def __init__(self, session, portal):
        self.session = session
        self.config = portals.pje(portal)
        self.origin = f"https://{self.config.host}"
        self._claims = None

    def _get(self, path, params=None, *, label, headers=None, stream=False, timeout=TIMEOUT):
        url = self.origin + path
        response = self.session.get(
            url,
            params=params,
            headers=headers or {},
            impersonate=IMPERSONATE,
            allow_redirects=False,
            timeout=timeout,
            stream=stream,
        )
        log_request("GET", url, response.status_code)
        if 300 <= response.status_code < 400:
            raise RuntimeError(f"{label} redirected; session may have expired")
        return response

    def get(self, path, params=None, *, label):
        response = self._get(API + path, params, label=label)
        if response.status_code == 401:
            raise SessionExpired(f"{label} refused (HTTP 401); the session expired")
        if response.status_code == 403:
            raise RuntimeError(f"{label} refused (HTTP 403); session may have expired")
        if response.status_code != 200:
            code = ""
            try:
                code = f" ({response.json().get('codigoErro', '')})"
            except (ValueError, AttributeError):
                pass
            raise RuntimeError(f"{label} returned HTTP {response.status_code}{code}")
        return response

    def json(self, path, params=None, *, label):
        try:
            return self.get(path, params, label=label).json()
        except ValueError:
            raise RuntimeError(f"{label} did not return JSON") from None

    @property
    def claims(self):
        """Identity of the logged-in user (``id``, ``instancia``); also renews the session's token cookies."""
        if self._claims is None:
            response = self._get(TOKEN, label="PJe token")
            if response.status_code not in (200, 201):
                raise RuntimeError(f"PJe token refresh returned HTTP {response.status_code}; not logged in")
            try:
                payload = response.json()["access_token"].split(".")[1]
                self._claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
            except (ValueError, KeyError, IndexError, AttributeError):
                raise RuntimeError("PJe token refresh returned no usable token") from None
        return self._claims

    @property
    def user(self):
        return self.claims["id"]

    @property
    def instance(self):
        return self.claims.get("instancia") or {"1g": 1, "2g": 2}.get(self.config.instance, 1)


def is_authenticated(session, portal):
    try:
        return bool(Api(session, portal).claims.get("id"))
    except (RuntimeError, RequestException):
        return False


# -- helpers -------------------------------------------------------------------


def _date(value):
    return value[:10] if value else None


def _br_date(value):
    try:
        moment = datetime.fromisoformat(value[:19])
    except (TypeError, ValueError):
        return value or ""
    return f"{moment:%d} {MONTHS[moment.month - 1]} {moment:%Y}"


def _time(value):
    return value[11:16] if value and len(value) >= 16 else ""


def _text(value):
    return " ".join(str(value or "").split())


# -- push ----------------------------------------------------------------------

PUSH_PAGE_SIZE = 50
MAX_PUSH_PAGES = 100


def read_push(session, portal, *, needed=None):
    """The processes registered in PJe Push ("Minhas assinaturas"), from ``/push`` with ``PROCESSOS_CADASTRADOS``.

    The legacy ``Push/listView.seam`` page of these portals is an empty shell of the old JSF app; the Angular
    "Push" route reads this endpoint. The "related" processes tab (``PROCESSOS_RELACIONADOS``) is not registrations
    and is not returned. ``needed`` stops the paging once that many rows are read (the caller wants one window of
    the list); ``results_count`` is still the total the portal reports.
    """
    api = Api(session, portal)
    items, total, pages, page = [], 0, 1, 1
    while page <= min(pages, MAX_PUSH_PAGES) and (needed is None or len(items) < needed):
        data = api.json(
            "/push",
            {
                "pagina": page,
                "tamanhoPagina": PUSH_PAGE_SIZE,
                "ordenacaoColuna": "dataInclusao",
                "ordenacaoAscendente": "false",
                "tipoConsulta": "PROCESSOS_CADASTRADOS",
                "filtroProcessoPessoaParte": "false",
            },
            label="PJe Push list",
        )
        pages = data.get("qtdPaginas") or 0
        total = data.get("totalRegistros") or 0
        for item in data.get("resultado") or []:
            items.append(
                {
                    "process_number": item.get("numeroProcesso") or "",
                    "registered_at": _date(item.get("dataInclusao")) or "",
                    "alternate_email": "",
                    "observation": "",
                    "process_id": item.get("idProcesso"),
                }
            )
        page += 1
    complete = len(items) >= total
    pagination = {
        "results_count": total,
        "current_page": min(page - 1, pages) or None,
        "total_pages": pages or None,
        "has_next_page": not complete,
        "complete": complete,
    }
    return {
        "success": True,
        "portal": portal,
        "push_url": f"https://{api.config.host}{API}/push",
        "pagination": pagination,
        **pagination,
        "rows_returned": len(items),
        "items": items,
        "content_note": "This page lists watched processes, not the individual movement email text.",
    }


# -- panel ---------------------------------------------------------------------


def parse_buckets(items):
    return [
        {
            "index": item["idAgrupamentoProcessoTarefa"],
            "label": _text(item["nomeAgrupamentoTarefa"]),
            "count": item["quantidadeProcessos"],
        }
        for item in sorted(items, key=lambda item: item.get("ordem", 0))
    ]


def read_counters(session, portal, *, tree=False, bucket=None):
    """The panel's buckets with counts; ``tree`` lists the bodies of every non-empty bucket, ``bucket`` of one."""
    api = Api(session, portal)
    buckets = parse_buckets(
        api.json(f"/paineladvogado/{api.user}/totalizadores", {"tipoPainelAdvogado": 0}, label="PJe panel")
    )
    result = {
        "success": True,
        "portal": portal,
        "panel_url": f"{api.origin}/pjekz/painel/usuario-externo",
        "buckets": buckets,
        "notifications": [],
    }
    only = _select_bucket(buckets, bucket) if bucket is not None else None
    if tree or only is not None:
        for item in buckets:
            if not item["count"] or (only is not None and item is not only):
                continue
            params = {"idPainelAdvogadoEnum": item["index"], "tipoPainelAdvogado": item["index"]}
            bodies = api.json(f"/paineladvogado/{api.user}/orgaojulgadores", params, label="PJe panel bodies")
            item["regions"] = [
                {
                    "path": [item["label"], _text(body.get("descricaoJurisdicao")), _text(body["descricao"])],
                    "count": None,
                    "kind": "Órgão julgador",
                    "inbox": True,
                }
                for body in bodies
            ]
    return result


def parse_row(item):
    """One panel row as an inbox row. ``dataCienciaParte`` empty means the ciência is still pending."""
    notice = _date(item.get("dataCienciaParte"))
    return {
        "expedienteId": str(item["idProcessoParteExpediente"]) if item.get("idProcessoParteExpediente") else None,
        "tipo": None,
        "parte": None,
        "meio": None,
        "expedidoEm": _date(item.get("dataCriacaoExpediente")),
        "ciencia": {"por": None, "em": notice} if notice else None,
        "cienciaPendente": bool(item.get("idProcessoParteExpediente")) and not notice,
        "prazoDias": None,
        "semPrazo": item.get("dataPrazoLegalParte") is None,
        "limite": _date(item.get("dataPrazoLegalParte")),
        "limiteTipo": "manifestação" if item.get("dataPrazoLegalParte") else None,
        "processNumber": item.get("numeroProcesso"),
        "classe": item.get("classeJudicial"),
        "processId": str(item["id"]) if item.get("id") is not None else None,
        "documentId": str(item["idDocumento"]) if item.get("idDocumento") else None,
        "orgaoJulgador": _text(item.get("descricaoOrgaoJulgadorColegiado") or item.get("descricaoOrgaoJulgador")),
        "resumo": "",
        "ultimoMovimento": None,
        "respondido": item.get("pendenteResposta") is False,
        "prazoVencido": item.get("prazoVencido"),
    }


def _select_bucket(buckets, wanted):
    """The bucket with this id or label text; no bucket given means Meus Expedientes.

    The ids are the panel's own (``idAgrupamentoProcessoTarefa``), not positions: an id the panel does not have,
    "0" included, is an error that lists the valid ones instead of a guess at what was meant.
    """
    wanted = MEUS_EXPEDIENTES if wanted is None else wanted
    for bucket in buckets:
        if str(wanted).isdigit() and bucket["index"] == int(wanted):
            return bucket
        if not str(wanted).isdigit() and str(wanted).casefold() in bucket["label"].casefold():
            return bucket
    valid = ", ".join(f"{bucket['index']} ({bucket['label']})" for bucket in buckets)
    raise ValueError(f"PJe panel has no bucket {wanted!r}; valid buckets on this portal: {valid}")


def _keep(row, filters):
    if filters.get("expedienteId") and row["expedienteId"] != str(filters["expedienteId"]):
        return False
    if filters.get("classe") and filters["classe"].casefold() not in (row["classe"] or "").casefold():
        return False
    day = row["expedidoEm"]
    if filters.get("from") and (not day or day < filters["from"]):
        return False
    return not (filters.get("to") and (not day or day > filters["to"]))


def read_inbox(session, portal, *, bucket=None, regions=None, filters=None, max_pages=25):
    """Rows of a bucket, grouped by órgão julgador (one inbox each), all pages up to ``max_pages``."""
    filters = filters or {}
    unknown = set(filters) - FILTERS
    if unknown:
        raise ValueError(f"unknown inbox filter(s): {sorted(unknown)}")
    api = Api(session, portal)
    buckets = parse_buckets(
        api.json(f"/paineladvogado/{api.user}/totalizadores", {"tipoPainelAdvogado": 0}, label="PJe panel")
    )
    selected = _select_bucket(buckets, bucket)
    brief = dict(selected)
    if not selected["count"]:
        return {"success": True, "portal": portal, "bucket": brief, "inboxes": [], "complete": True}
    params = {
        "idPainelAdvogadoEnum": selected["index"],
        "tipoPainelAdvogado": selected["index"],
        "tamanhoPagina": PAGE_SIZE,
    }
    if filters.get("processNumber"):
        parse_process_number(filters["processNumber"])
        params["numeroProcesso"] = filters["processNumber"]
    rows, total, pages = [], 0, 1
    page = 1
    while page <= min(pages, max_pages):
        data = api.json(f"/paineladvogado/{api.user}/processos", {**params, "pagina": page}, label="PJe panel list")
        pages = data.get("qtdPaginas") or 0
        total = data.get("totalRegistros") or 0
        rows.extend(parse_row(item) for item in data.get("resultado") or [])
        page += 1
    complete = len(rows) >= total
    groups, kept = {}, []
    for row in rows:
        path = [selected["label"], row["orgaoJulgador"]]
        if regions and not any(r.casefold() in " / ".join(path).casefold() for r in regions):
            continue
        # Every filter reads what the panel list itself carries, so rows are dropped before the per-process reads.
        if _keep(row, filters):
            groups.setdefault(row["orgaoJulgador"], []).append(row)
            kept.append(row)
    _enrich_rows(api, kept)
    inboxes = [
        {
            "path": [selected["label"], name],
            "total": len(items),
            "pages": pages,
            "returned": len(items),
            "complete": complete,
            "changedWhileReading": False,
            "rows": items,
        }
        for name, items in groups.items()
    ]
    return {"success": True, "portal": portal, "bucket": brief, "inboxes": inboxes, "complete": complete}


ACERVO_BUCKETS = ("acervo", "arquivad")


def _parties(item):
    """ "AUTHOR X DEFENDANT" as the panel shows it, with "e outros" when a side has more than one party."""
    sides = []
    for name, count in (("nomeParteAutora", "qtdeParteAutora"), ("nomeParteRe", "qtdeParteRe")):
        if item.get(name):
            sides.append(_text(item[name]) + (" e outros" if (item.get(count) or 1) > 1 else ""))
    return " X ".join(sides) or None


def read_acervo(session, portal, *, regions=None, max_pages=25):
    """The lawyer's processes from the panel's Acervo Geral and Arquivados buckets, all pages up to ``max_pages``.

    The same panel list as the expediente buckets: number, class, parties, órgão julgador and the filing and archiving
    dates; it shows no last movement.
    """
    api = Api(session, portal)
    buckets = parse_buckets(
        api.json(f"/paineladvogado/{api.user}/totalizadores", {"tipoPainelAdvogado": 0}, label="PJe panel")
    )
    selected = [b for b in buckets if any(word in b["label"].casefold() for word in ACERVO_BUCKETS)]
    if not selected:
        raise ValueError(f"PJe panel has no Acervo bucket; buckets on this portal: {[b['label'] for b in buckets]}")
    jurisdictions, items = [], {}
    for bucket in selected:
        params = {"idPainelAdvogadoEnum": bucket["index"], "tipoPainelAdvogado": bucket["index"]}
        rows, total, pages, page = 0, bucket["count"] or 0, 1, 1
        while bucket["count"] and page <= min(pages, max_pages):
            data = api.json(
                f"/paineladvogado/{api.user}/processos",
                {**params, "tamanhoPagina": PAGE_SIZE, "pagina": page},
                label="PJe panel list",
            )
            pages, total = data.get("qtdPaginas") or 0, data.get("totalRegistros") or 0
            for item in data.get("resultado") or []:
                row = parse_row(item)
                rows += 1
                if regions and not any(t.casefold() in (row["orgaoJulgador"] or "").casefold() for t in regions):
                    continue
                items.setdefault(
                    row["processId"] or row["processNumber"],
                    {
                        "processNumber": row["processNumber"],
                        "processId": row["processId"],
                        "classe": row["classe"],
                        "partes": _parties(item),
                        "orgaoJulgador": row["orgaoJulgador"],
                        "distribuidoEm": _date(item.get("dataAutuacao")),
                        "arquivadoEm": _date(item.get("dataArquivamento")),
                        "jurisdicao": row["orgaoJulgador"],
                        "caixa": bucket["label"],
                    },
                )
            page += 1
        jurisdictions.append(
            {"label": bucket["label"], "count": bucket["count"], "pages": pages, "complete": rows >= total}
        )
    return {
        "success": True,
        "portal": portal,
        "source": "acervo",
        "jurisdictions": jurisdictions,
        "total": sum(b["count"] or 0 for b in selected),
        "returned": len(items),
        "items": list(items.values()),
        "complete": all(j["complete"] for j in jurisdictions),
    }


ROW_DETAILS = ("tipo", "parte", "meio", "prazoDias", "semPrazo")


def _enrich_rows(api, rows):
    """Fill type, recipient, medium and days of panel rows from their process' expedientes (one GET per process).

    The panel list carries none of these; the process' expedientes list does. A failed read leaves the rows as they
    were and marks them with ``detalhesIndisponiveis``.
    """
    by_process = {}
    for row in rows:
        if row.get("expedienteId") and row.get("processId"):
            by_process.setdefault(row["processId"], []).append(row)
    for process_id, items in by_process.items():
        try:
            expedientes, _ = read_expedientes(api, process_id)
        except SessionExpired:
            raise
        except RuntimeError:
            for row in items:
                row["detalhesIndisponiveis"] = True
            continue
        found = {item["expedienteId"]: item for item in expedientes}
        for row in items:
            detail = found.get(row["expedienteId"])
            if detail is None:
                row["detalhesIndisponiveis"] = True
                continue
            row.update({key: detail[key] for key in ROW_DETAILS})


# -- process -------------------------------------------------------------------


def parse_timeline(items):
    """Days (newest first) of movements and documents; attachments are listed with their parent document."""
    days, order = {}, []
    for item in items:
        day = _br_date(item.get("data"))
        if day not in days:
            days[day] = {"date": day, "movements": []}
            order.append(day)
        title = _text(item.get("titulo"))
        documents = []
        if item.get("documento"):
            documents.append({"id": str(item["id"]), "title": title})
            documents.extend(
                {"id": str(a["id"]), "title": _text(a.get("titulo")), "attachment_of": str(item["id"])}
                for a in item.get("anexos") or []
            )
        days[day]["movements"].append(
            {
                "time": _time(item.get("data")),
                "movement": _text(item.get("tipo")) or title if item.get("documento") else title,
                "movement_texts": [title],
                "documents": documents,
                "entry_type": "document" if item.get("documento") else "movement",
                "type_code": "D" if item.get("documento") else "M",
                "origin": item.get("tipoPolo"),
                "origin_code": item.get("codigoMovimentoCNJ"),
            }
        )
    return [days[day] for day in order]


def parse_inventory(items):
    inventory = []
    for item in items:
        if not item.get("documento"):
            continue
        for entry in [item, *(item.get("anexos") or [])]:
            inventory.append(
                {
                    "id": str(entry["id"]),
                    "title": _text(entry.get("titulo")),
                    "type": entry.get("tipo"),
                    "idUnico": entry.get("idUnicoDocumento"),
                    "date": _date(entry.get("data")),
                    "visible_in_timeline": True,
                }
            )
    return inventory


def parse_expedientes(items):
    """The process' communications (one per recipient) in the shape the JSF Expedientes tab gives."""
    result = []
    for item in items:
        notice = _date(item.get("dataCiencia"))
        days = item.get("prazoLegal")
        without = item.get("tipoPrazoEnum") == "SEM_PRAZO" or not days
        limit = _date(item.get("fimDoPrazoLegal"))
        result.append(
            {
                "expedienteId": str(item["id"]),
                "tipo": item.get("tipoExpediente"),
                "documentId": str(item["idDocumento"]) if item.get("idDocumento") else None,
                "parte": item.get("nomePessoaParte"),
                "meio": item.get("meioExpediente"),
                "expedidoEm": _date(item.get("dataCriacao")),
                "ciencia": {"por": "sistema" if item.get("cienciaViaSistema") else None, "em": notice}
                if notice
                else None,
                "prazoDias": None if without else days,
                "semPrazo": without,
                "limite": limit,
                "limiteTipo": "manifestação" if limit else None,
                "respondido": False,
                "fechado": item.get("fechado"),
                "atoPath": f"documento/{item['idDocumento']}" if item.get("idDocumento") else None,
                "respostaPath": None,
            }
        )
    return result


def read_expedientes(api, process_id):
    """(every communication of the process, whether that is provably all of them).

    The endpoint answers in pages (``pagina``, ``qtdPaginas``, ``totalRegistros``; 20 rows each). The first request
    is sent as the app sends it; further pages are asked with ``pagina``, the parameter the other paged lists of
    this API take. The list is complete only when every page answered with the page asked for and the rows read
    reach the total the portal reports. Anything else (a page parameter the endpoint ignores, no total, more pages
    than ``MAX_EXPEDIENTE_PAGES``) is incomplete, and the ciência check then refuses instead of trusting it.
    """
    path, params = f"/processos/id/{process_id}/expedientes", {"instancia": api.instance}
    data = api.json(path, params, label="PJe expedientes")
    if not isinstance(data, dict):
        raise RuntimeError("PJe expedientes did not return a paged list")
    rows = {item["id"]: item for item in data.get("resultado") or []}
    total, pages = data.get("totalRegistros"), data.get("qtdPaginas") or 1
    in_order = data.get("pagina") in (None, 1)
    for page in range(2, min(pages, MAX_EXPEDIENTE_PAGES) + 1):
        data = api.json(path, {**params, "pagina": page}, label="PJe expedientes")
        if not isinstance(data, dict) or data.get("pagina") != page:
            in_order = False
            break
        rows.update({item["id"]: item for item in data.get("resultado") or []})
    complete = in_order and isinstance(total, int) and len(rows) >= total
    return parse_expedientes(list(rows.values())), complete


def parse_parties(data):
    names = {"ATIVO": "active", "PASSIVO": "passive", "TERCEIROS": "other_interested", "OUTROS": "other_interested"}
    result = {"active": [], "passive": [], "other_interested": []}
    for polo, entries in (data or {}).items():
        target = result[names.get(polo.upper(), "other_interested")]
        for party in entries:
            target.append(
                _text(f"{party.get('nome')} ({party.get('tipo')})" if party.get("tipo") else party.get("nome"))
            )
    return result


def parse_details(process):
    body = process.get("orgaoJulgador") or {}
    klass = process.get("classeJudicial") or {}
    return {
        "judicial_class": f"{klass.get('descricao')} ({klass.get('sigla')})" if klass else None,
        "jurisdiction": (process.get("jurisdicao") or {}).get("descricao"),
        "filed_at": _date(process.get("autuadoEm")),
        "last_distribution": _date(process.get("distribuidoEm")),
        "case_value": process.get("valorDaCausa"),
        "confidential": process.get("segredoDeJustica"),
        "digital_court": process.get("juizoDigital"),
        "legal_aid": process.get("justicaGratuita"),
        "injunction": process.get("tutelaOuLiminar"),
        "judicial_body": body.get("descricao"),
        "phase": process.get("labelFaseProcessual"),
        "status": process.get("labelStatusProcesso"),
    }


# As ``search._memo`` for the JSF portals: get_process, read_documents and read_expediente_ato on one process each
# looked it up and read its record, parties and timeline again. Those are kept a few minutes per session; the
# expedientes are read on every call, because opening a document or an act may register a ciência.
MEMO_SECONDS = 180
_memo = weakref.WeakKeyDictionary()  # session -> {process number: (time, process id, record, parties, timeline)}
_memo_guard = threading.Lock()


def _recall(session, number):
    with _memo_guard:
        entry = _memo.get(session, {}).get(number)
    if entry is None or time.monotonic() - entry[0] > MEMO_SECONDS:
        return None
    return copy.deepcopy(entry[1:])


def _remember(session, number, process_id, process, parties, raw):
    try:
        with _memo_guard:
            _memo.setdefault(session, {})[number] = (
                time.monotonic(),
                process_id,
                *copy.deepcopy((process, parties, raw)),
            )
    except TypeError:
        pass  # a session that cannot be weakly referenced is not remembered


def _process_id(api, number):
    response = api.get("/processos", {"numero": number}, label="PJe process lookup")
    found = response.text.strip().strip('"')
    return found if re.fullmatch(r"\d+", found) else None


def search_process(
    session,
    process_number,
    raw_html_path=None,
    *,
    portal,
    ato_expediente=None,
    ato_text_limit=20000,
    ato_allow_pending=False,
):
    """The ``search.search_process`` result for a KZ portal. ``raw_html_path`` is ignored: there is no HTML."""
    from jurisync.connectors.pje.expedientes import PendingNotice, ato_ciencia, ato_item, pending_message

    parts = parse_process_number(process_number)
    canonical = "{}-{}.{}.{}.{}.{}".format(*parts)
    api = Api(session, portal)
    diagnostics = []
    kept = _recall(session, canonical)
    if kept is not None:
        process_id, process, parties, raw = kept
    else:
        process_id = _process_id(api, canonical)
        if not process_id:
            return {"success": False, "error": "Process not found or not visible to this user", "timeline": []}
        process = api.json(f"/processos/id/{process_id}", label="PJe process")
        if process.get("numero") != canonical:
            return {
                "success": False,
                "error": "Process returned does not match the requested number",
                "processo_id": process_id,
                "timeline": [],
            }
        try:
            parties = parse_parties(api.json(f"/processos/id/{process_id}/partes", label="PJe parties"))
        except RuntimeError as error:
            parties = None
            diagnostics.append({"code": "parties_unavailable", "message": str(error)[:200]})
        raw = api.json(
            f"/processos/id/{process_id}/timeline",
            {"somenteDocumentosAssinados": "false", "buscarMovimentos": "true", "buscarDocumentos": "true"},
            label="PJe timeline",
        )
        if parties is not None:
            _remember(session, canonical, process_id, process, parties, raw)  # an incomplete read is not kept
        parties = parties or {}
    truncated = len(raw) > MAX_TIMELINE
    raw = raw[:MAX_TIMELINE]
    timeline = parse_timeline(raw)
    first, count = [], 0
    for day in timeline:
        if count >= FIRST_PAGE_EVENTS:
            break
        first.append(day)
        count += len(day["movements"])
    try:
        expedientes, expedientes_complete = read_expedientes(api, process_id)
    except SessionExpired:
        raise
    except RuntimeError as error:
        expedientes, expedientes_complete = None, False
        diagnostics.append({"code": "expedientes_unavailable", "message": str(error)[:200]})
    if expedientes is not None and not expedientes_complete:
        diagnostics.append({"code": "expedientes_incomplete", "message": "the expedientes list was not read in full"})
    ato = ciencia = None
    if ato_expediente:
        item = None
        if expedientes is not None:
            ato = {"expedienteId": str(ato_expediente)}
            try:
                item = ato_item(expedientes, ato_expediente)
                if not item.get("ciencia") and not ato_allow_pending:
                    raise PendingNotice(pending_message([item]))
                ato.update(_ato(api, process_id, canonical, item, ato_text_limit))
            except SessionExpired:
                raise
            except (RuntimeError, ValueError) as error:
                ato["error"] = str(error)[:300]
        ciencia = ato_ciencia(item, ato is not None and "error" not in ato)
    documents = sum(len(m["documents"]) for d in timeline for m in d["movements"])
    movements = sum(len(d["movements"]) for d in timeline)
    return {
        "success": True,
        "processo_id": process_id,
        "numero_processo": canonical,
        "detail_status": 200,
        "days": len(timeline),
        "movements": movements,
        "documents": documents,
        "process_details": parse_details(process),
        "parties": parties,
        "timeline": timeline,
        "first_timeline": first,
        "document_inventory": parse_inventory(raw),
        "pagination": {
            "page": 1,
            "mode": "api",
            "loaded_days": len(timeline),
            "loaded_movements": movements,
            "loaded_documents": documents,
            "more_pages_possible": False,
            "complete": not truncated,
            "limit": MAX_TIMELINE if truncated else None,
        },
        "latest_update": timeline[0] if timeline else None,
        "expedientes": expedientes,
        "expedientes_complete": expedientes_complete,
        "ato": ato,
        **({"ciencia": ciencia} if ciencia is not None else {}),
        "diagnostics": diagnostics,
    }


def _ato(api, process_id, number, item, limit):
    """The act of an expediente whose ciência is registered (or acknowledged): its document's text."""
    document_id = (item.get("atoPath") or "").rpartition("/")[2]
    if not document_id:
        raise ValueError("this expediente has no readable act link")
    read = fetch_document(api, process_id, document_id)
    status, text = (
        extract_document_text(read["content"], read["content_type"], number, allow_unnumbered=True)
        if read["content"] is not None
        else (read["status"], "")
    )
    return {
        "expediente": item,
        "anexos": [],
        "texto": text[:limit],
        "truncado": len(text) > limit,
        "extractionStatus": status,
    }


# -- documents -----------------------------------------------------------------


# As ``documents._download``: a portal may render a large PDF for a minute before its first byte.
DOCUMENT_TIMEOUT = (15, 150)


def fetch_document(api, process_id, document_id):
    """GET the PDF of a joined document, capped at ``MAX_DOCUMENT_BYTES``; ``content`` is None when not read."""
    out = {"status": None, "content_type": "", "bytes": 0, "content": None}
    response = api._get(
        f"{API}/processos/id/{process_id}/documentos/id/{document_id}/conteudo",
        {"incluirAssinatura": "false", "incluirCapa": "false"},
        label="PJe document",
        headers={"Accept": "application/pdf,text/html,*/*"},
        stream=True,
        timeout=DOCUMENT_TIMEOUT,
    )
    try:
        out.update(status=response.status_code, content_type=response.headers.get("Content-Type", ""))
        if response.status_code != 200:
            return out
        content = bytearray()
        for chunk in response.iter_content():
            out["bytes"] += len(chunk)
            if out["bytes"] > MAX_DOCUMENT_BYTES:
                out["status"] = "too_large"
                return out
            content.extend(chunk)
        out["content"] = bytes(content)
        return out
    finally:
        response.close()


def read_documents(process, document_ids, *, session, save=False):
    """``documents.read_selected_documents`` for a KZ portal; ``process`` is the successful ``search_process`` result."""
    from jurisync.connectors.pje.documents import _saved_name

    portal = process.get("portal")
    config = portals.pje(portal)
    number = process.get("numero_processo")
    if (
        process.get("success") is not True
        or not isinstance(number, str)
        or not config.owns_number(parse_process_number(number))
    ):
        raise ValueError("A successful supported PJe process result is required")
    validate_document_ids(document_ids)
    process_id = process.get("processo_id")
    if not isinstance(process_id, str) or not re.fullmatch(r"[0-9]+", process_id):
        raise ValueError("The adapter's numeric process ID is required")
    known = {d["id"]: d for d in process.get("document_inventory") or []}
    for doc_id in document_ids:
        if doc_id not in known:
            raise ValueError("Selected document ID is not in this process")
    api = Api(session, portal)
    results = []
    for doc_id in document_ids:
        item = {
            "id": doc_id,
            "title": known[doc_id].get("title", ""),
            "status": None,
            "content_type": "",
            "bytes": 0,
            "extractionStatus": "request_failed",
            "text": "",
        }
        step = "request"
        try:
            read = fetch_document(api, process_id, doc_id)
            item.update(status=read["status"], content_type=read["content_type"], bytes=read["bytes"])
            if read["content"] is None:
                if read["status"] == "too_large":
                    item["extractionStatus"] = "too_large"
                else:
                    item.update(extractionStatus="http_error", failure=f"HTTP {read['status']}")
            else:
                content = read["content"]
                item["sha256"] = hashlib.sha256(content).hexdigest()
                item["extractionStatus"], item["text"] = extract_document_text(
                    content, item["content_type"], number, allow_unnumbered=True
                )
                step = "save"
                if save:
                    item["savedAs"] = save_document(_saved_name(number, doc_id, content, item), content)
        except (RequestException, OSError) as error:
            # As ``documents.read_selected_documents``: the step and the error's class, never its URL-bearing message.
            item.update(extractionStatus="request_failed", text="", failure=f"{step} failed: {type(error).__name__}")
        results.append(item)
    return results
