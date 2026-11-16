"""Tucujuris reads: the account's processes, one process (header, parties, movements, files) and comunicações.

Field names follow the portal's JSON as the SPA templates bind them (``cabecalhoprocesso``, ``historicoprocesso``,
``arquivosprocesso``, ``processos-envolvido``, ``comunicacoes-processuais``). Only what a lawyer needs is kept:
names, roles, dates, descriptions and ids; no CPF, address or internal key leaves this module. Table/column names
and consumption keys needed to download a file stay in the index, never in a tool result.

Ciência: an unread comunicação has no ``dt_intimacao``; the portal records its ciência only when the lawyer marks it
read (``processar-comunicacao``, refused by ``client.guard``) or when the deadline for tacit ciência passes. To keep a
read from ever looking like opening the act, files of a movement that originated an unread comunicação are not read
or downloaded (``CienciaPending``); they are listed with ``pendingCiencia: true``.
"""

import hashlib
import re

from jurisync import portals
from jurisync.connectors.tucujuris import client
from jurisync.core.document_text import extract_document_text
from jurisync.core.documents import validate_document_ids
from jurisync.core.documents_dir import save_document

MAX_TEXT_CHARACTERS = 60_000
DOCUMENT_ID = re.compile(r"[0-9]{1,20}(?:-[0-9]{1,3})?")
PERIODS = ("hoje", "ontem", "ultimos10dias")
MONTH = re.compile(r"(\d{4})-(0[1-9]|1[0-2])")
SITUATIONS = ("naolidas", "lidas")
POLES = {0: "ativo", 1: "passivo"}


class CienciaPending(ValueError):
    """A file of a movement whose comunicação is still unread: refused so the read cannot count as ciência."""


class ProcessNotFound(LookupError):
    pass


def cnj(number):
    """The formatted CNJ number of a TJAP process (J=8, TR=03); ``ValueError`` otherwise."""
    digits = re.sub(r"\D", "", number or "")
    if len(digits) != 20:
        raise ValueError("process_number must be a CNJ number such as 0000000-00.2026.8.03.0001")
    formatted = f"{digits[:7]}-{digits[7:9]}.{digits[9:13]}.{digits[13]}.{digits[14:16]}.{digits[16:]}"
    if digits[13:16] != "803":
        raise ValueError("Tucujuris serves TJAP processes only (CNJ segment .8.03.)")
    return formatted


def _text(value):
    return re.sub(r"\s+", " ", str(value)).strip() if value not in (None, "") else None


def _id(value):
    text = _text(value)
    return text if text and re.fullmatch(r"[0-9]{1,20}", text) else None


def _compact(mapping):
    return {key: value for key, value in mapping.items() if value not in (None, "", [], {})}


# --- Parsers ----------------------------------------------------------------------------------------------------------


def party(item):
    return _compact(
        {
            "name": _text(item.get("nome")),
            "role": _text(item.get("desc_tipo_parte")),
            "pole": POLES.get(item.get("tipo_parte")),
            "main": True if item.get("parteprincipal") else None,
        }
    )


def header(cabecalho):
    cabecalho = cabecalho or {}
    return _compact(
        {
            "processId": _id(cabecalho.get("id")),
            "number": _text(cabecalho.get("numero_cnj")),
            "class": _text(cabecalho.get("classe")),
            "rite": _text(cabecalho.get("rito")),
            "district": _text(cabecalho.get("comarca")),
            "court": _text(cabecalho.get("lotacao")),
            "rapporteur": _text(cabecalho.get("relator")),
            "archived": bool(cabecalho.get("arquivado")),
            "sealed": bool(cabecalho.get("segredo_justica")),
            "electronic": bool(cabecalho.get("virtual")),
            "suspended": _text(cabecalho.get("autos_suspenso")),
            "parties": [party(item) for item in cabecalho.get("partes") or [] if isinstance(item, dict)],
        }
    )


def summary(item):
    """One entry of "Meus processos" (``buscar-autos-pesssoa-esta-envolvida``)."""
    result = header(item.get("cabecalho"))
    result.setdefault("processId", _id(item.get("id")))
    result.update(
        _compact(
            {
                "involvement": _text(item.get("descricao_envolvimento")),
                "lastMovement": _compact(
                    {
                        "description": _text(item.get("ultimo_movimento_descricao")),
                        "date": _text(item.get("ultimo_movimento_dt_andamento")),
                    }
                ),
                "nextDeadline": _compact(
                    {
                        "date": _text(item.get("proximo_movimento_prazo")),
                        "recipient": _text(item.get("proximo_movimento_destinatario")),
                        "purpose": _text(item.get("proximo_movimento_finalidade")),
                    }
                ),
            }
        )
    )
    return result


def cover(item):
    """One row of ``detalhe.capa``: a party with its lawyers."""
    return _compact(
        {
            "role": _text(item.get("tipoparte")),
            "name": _text(item.get("nomeparte")),
            "lawyersLabel": _text(item.get("titulo_documentos")),
            "lawyers": _text(item.get("nome_adv")),
        }
    )


def movement(item):
    return _compact(
        {
            "movementId": _id(item.get("id_pa")),
            "sequence": item.get("ordem"),
            "date": _text(item.get("dt_andamento_pa")),
            "description": _text(item.get("descricao_pa")),
            "complement": _text(item.get("complemento_pa")),
            "deadline": _text(item.get("dt_prazo_pa")),
            "fileCount": item.get("qtd_anexo") or None,
            "cancelled": True if item.get("cancelado") else None,
        }
    )


def index_files(anexos):
    """``{documentId: file}`` for the attachments of a process or movement, in portal order.

    The id is the attachment's own ``id`` (the row id when it has none); a repeated id gets a ``-2``, ``-3`` suffix.
    """
    files, seen = {}, {}
    for anexo in anexos or []:
        if not isinstance(anexo, dict):
            continue
        base = _id(anexo.get("id")) or _id(anexo.get("id_tabela"))
        if base is None:
            continue
        seen[base] = seen.get(base, 0) + 1
        identifier = base if seen[base] == 1 else f"{base}-{seen[base]}"
        stream = _text(anexo.get("link_stream"))
        if stream and (stream.startswith(("http:", "https:", "//")) or ".." in stream):
            stream = None  # only repository paths on the portal's own host are followed
        files[identifier] = {
            "id": identifier,
            "name": _text(anexo.get("nome_anexo")) or _text(anexo.get("nome_arquivo")) or identifier,
            "movementId": _id(anexo.get("id_movimento")) or _id((anexo.get("movimento") or {}).get("id_pa")),
            "date": _text(anexo.get("dt_movimento")),
            "extension": _text(anexo.get("extensao_arquivo")),
            "table": _text(anexo.get("nome_tabela")),
            "row": _text(anexo.get("id_tabela")),
            "column": _text(anexo.get("nome_campo_anexo")),
            "key": _text(anexo.get("chave_consumo")),
            "stream": stream,
            "external": bool(anexo.get("midia_fora_do_tucujuris")) or bool(anexo.get("url_arquivo") and not stream),
        }
    return files


def public_file(file, pending=()):
    return _compact(
        {
            "documentId": file["id"],
            "name": file["name"],
            "movementId": file["movementId"],
            "date": file["date"],
            "extension": file["extension"],
            "outsideTucujuris": True if file["external"] else None,
            "pendingCiencia": True if file["movementId"] in pending else None,
        }
    )


def comunicacao(item):
    read = bool(item.get("dt_intimacao"))
    return _compact(
        {
            "intimacaoId": _id(item.get("autos_pessoa_intimacao_id")),
            "type": _text(item.get("descricao_tipo_notificacao")),
            "processId": _id(item.get("processo_id")),
            "number": _text(item.get("numero_cnj")),
            "movementId": _id(item.get("id_movimento_origem")),
            "movementSequence": item.get("ordem_movimento_origem"),
            "registeredAt": _text(item.get("dt_cad")),
            "cienciaAt": _text(item.get("dt_intimacao")),
            "deadline": _text(item.get("dt_prazo_movimento_comunicacao")),
            "object": _text(item.get("objeto_notificacao")),
            "fileCount": item.get("qtd_anexos_movimento_origem") or None,
            "read": read,
        }
    )


# --- Reads ------------------------------------------------------------------------------------------------------------


def my_processes(session, archived=False):
    """The processes the account is involved in ("Meus processos"), as the page filters them."""
    items = client.get(
        session,
        "autos/buscar-autos-pesssoa-esta-envolvida",
        {"situacao_processo": 1 if archived else 0},
        page="processos-envolvido/processos-envolvido.html",
    )
    result = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        is_archived = bool((item.get("cabecalho") or {}).get("arquivado"))
        if is_archived == archived:
            result.append(item)
    return result


def _digits(number):
    return re.sub(r"\D", "", number or "")


def find_process(session, number):
    """``(autos_id, chave_consumo)`` for a CNJ number: the account's own processes first, then the public search."""
    wanted = _digits(number)
    cached = session.cache.get(wanted)
    if cached:
        return cached
    for archived in (False, True):
        for item in my_processes(session, archived):
            if _digits((item.get("cabecalho") or {}).get("numero_cnj")) == wanted:
                found = (_id(item.get("id")) or _id((item.get("cabecalho") or {}).get("id")), None)
                if found[0]:
                    session.cache[wanted] = found
                    return found
    if session.passe is None:
        session.passe = client.get(session, "publico/buscar-passe-captcha") or ""
    if not session.passe:
        raise ProcessNotFound(
            f"{number} is not among this account's processes, and Tucujuris asks a CAPTCHA for any other search"
        )
    data = client.get(
        session,
        "publico/buscar-autos-consulta-publica",
        {"numero_unico": number, "situacao_processo": "", "captcha": session.passe},
        page="consultar-processo/consultar-processo.html",
    )
    for item in (data or {}).get("autos") or []:
        cabecalho = item.get("cabecalho") or {}
        if _digits(cabecalho.get("numero_cnj")) == wanted:
            found = (_id(item.get("id")) or _id(cabecalho.get("id")), _text(item.get("chave_consumo")))
            if found[0]:
                session.cache[wanted] = found
                return found
    raise ProcessNotFound(f"{number} was not found in Tucujuris")


def _consulta(session, path, autos_id, key, **extra):
    data = client.get(
        session,
        path,
        {"autos_id": autos_id, "chave_consumo": key, **extra},
        page="detalhes-processo/detalhes-processo.html",
    )
    if isinstance(data, dict) and data.get("requer_chave_consumo"):
        raise client.TucujurisError("Tucujuris asks a CAPTCHA to open this process; the connector never solves one")
    return data or {}


def pending_comunicacoes(session, number):
    """Unread comunicações of one process (``situacao=porprocesso``); a GET, never the "Marcar como lido" POST."""
    items = client.get(
        session,
        "comunicacoes-processuais/buscar-comunicacoes",
        {"situacao": "porprocesso", "numero_unico": number},
        page="comunicacoes-processuais/comunicacoes-processuais.html",
    )
    return [item for item in items or [] if isinstance(item, dict) and not item.get("dt_intimacao")]


def load_process(session, process_number, documents=True):
    """``(process, files, pending_movement_ids)`` for one process."""
    number = cnj(process_number)
    autos_id, key = find_process(session, number)
    detail = _consulta(session, "publico/buscar-detalhes-autos-consulta-publica", autos_id, key)
    movements = detail.get("movimentos")
    if movements is None:
        movements = _consulta(session, "publico/buscar-movimentos-autos-consulta-publica", autos_id, key).get(
            "movimentos"
        )
    process = header(detail.get("cabecalho"))
    process.setdefault("number", number)
    process.setdefault("processId", autos_id)
    cover_rows = [cover(row) for row in detail.get("capa") or [] if isinstance(row, dict)]
    if cover_rows:
        process["parties"] = cover_rows
    process["movements"] = [movement(item) for item in movements or [] if isinstance(item, dict)]
    pending = {_id(item.get("id_movimento_origem")) for item in pending_comunicacoes(session, number)} - {None}
    files, suppressed = {}, None
    if documents:
        data = _consulta(session, "publico/buscar-anexos-processo-consulta-publica", autos_id, key)
        files = index_files(data.get("anexos"))
        suppressed = data.get("total_anexos_suprimidos") or None
    for item in process["movements"]:
        if item.get("movementId") in pending:
            item["pendingCiencia"] = True
    process["pendingCienciaMovements"] = sorted(pending)
    if suppressed:
        process["suppressedFiles"] = suppressed
    process["_autos"] = (autos_id, key)
    return process, files, pending


def movement_files(session, process, movement_id):
    autos_id, key = process["_autos"]
    data = _consulta(
        session, "publico/buscar-anexos-movimento-consulta-publica", autos_id, key, movimento_id=movement_id
    )
    return index_files(data.get("anexos"))


def file_format(content, content_type):
    if content.lstrip().startswith(b"%PDF-") or "pdf" in (content_type or "").casefold():
        return "pdf"
    return "html" if "html" in (content_type or "").casefold() else "other"


def _selected(files, pending, document_ids):
    validate_document_ids(document_ids, pattern=DOCUMENT_ID)
    if any(identifier not in files for identifier in document_ids):
        raise ValueError("Selected document ID is not in this process")
    blocked = [identifier for identifier in document_ids if files[identifier]["movementId"] in pending]
    if blocked:
        raise CienciaPending(
            f"document(s) {', '.join(blocked)} belong to a movement with an unread comunicação; reading them could "
            "count as ciência, so they are not opened. The comunicação stays pending until the lawyer reads it in "
            "Tucujuris"
        )
    return [files[identifier] for identifier in document_ids]


def read_documents(session, process_number, document_ids):
    process, files, pending = load_process(session, process_number)
    results = []
    for file in _selected(files, pending, document_ids):
        result = {"documentId": file["id"], "name": file["name"], "movementId": file["movementId"]}
        results.append(result)
        content, content_type = client.download(session, file)
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
    return process["number"], results


def download_document(session, process_number, document_id):
    process, files, pending = load_process(session, process_number)
    (file,) = _selected(files, pending, [document_id])
    content, content_type = client.download(session, file)
    result = {"documentId": file["id"], "name": file["name"], "movementId": file["movementId"]}
    if content is None:
        return {**result, "saved": False, "reason": content_type}
    kind = file_format(content, content_type)
    status, _ = extract_document_text(content, content_type, process["number"], allow_unnumbered=True)
    result.update(
        {
            "format": kind,
            "bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
            "extractionStatus": status,
        }
    )
    if status in ("access_denied", "session_expired"):
        return {**result, "saved": False}
    extension = {"pdf": "pdf", "html": "html"}.get(kind, (file["extension"] or "bin").lower()[:5])
    if not re.fullmatch(r"[a-z0-9]{1,5}", extension):
        extension = "bin"
    result["savedAs"] = save_document(f"{_digits(process['number'])}_{file['id']}.{extension}", content)
    result["saved"] = True
    return result


def comunicacoes(session, situacao="naolidas", periodo="ultimos10dias", process_number=None):
    """Comunicações processuais (intimações, citações, ...) addressed to the account. Listing never marks them read."""
    page = "comunicacoes-processuais/comunicacoes-processuais.html"
    if process_number:
        number = cnj(process_number)
        items = client.get(
            session, "comunicacoes-processuais/buscar-comunicacoes", {"situacao": "porprocesso", "numero_unico": number},
            page=page,
        )  # fmt: skip
        listed = [comunicacao(item) for item in items or [] if isinstance(item, dict)]
        return {"processNumber": number, "comunicacoes": listed}
    if situacao not in SITUATIONS:
        raise ValueError(f"situacao must be one of {', '.join(SITUATIONS)}")
    month = MONTH.fullmatch(periodo or "")
    if periodo not in PERIODS and not month:
        raise ValueError("periodo must be hoje, ontem, ultimos10dias or a month YYYY-MM")
    portal_period = f"{month.group(1)}-{month.group(2)}-01" if month else periodo
    counts = client.get(
        session, "comunicacoes-processuais/buscar-periodos-comunicacoes", {"situacao": situacao}, page=page
    )
    types = client.get(
        session,
        "comunicacoes-processuais/buscar-tipos-comunicacao-periodo",
        {"situacao": situacao, "periodo": portal_period},
        page=page,
    )
    codes = [item.get("codigo_tipo_notificacao") for item in types or [] if isinstance(item, dict)]
    codes = [code for code in codes if code not in (None, "")]
    items = []
    if codes:
        items = client.get(
            session,
            "comunicacoes-processuais/buscar-comunicacoes",
            {"situacao": situacao, "periodo": portal_period, "tipos": codes},
            page=page,
        )
    return {
        "situacao": situacao,
        "periodo": periodo,
        "counts": {key: value for key, value in (counts or {}).items() if key in PERIODS}
        if isinstance(counts, dict)
        else {},
        "types": [
            _compact({"type": _text(item.get("descricao_tipo_notificacao")), "count": item.get("qtd")})
            for item in types or []
            if isinstance(item, dict)
        ],
        "comunicacoes": [comunicacao(item) for item in items or [] if isinstance(item, dict)],
    }


def portal_key():
    return portals.tucujuris(client.PORTAL).key
