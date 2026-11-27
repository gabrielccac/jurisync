"""Ciência block of eproc process reads (``core.ciencia.report``).

Opening the document of a citação/intimação whose deadline was not opened yet may make the lawyer count as notified,
which starts the deadline. Eproc documents are always read (owner's decision, 2026-11-09); before anything is opened
the client reads the lawyer's pending queues (the panel's "citações/intimações pendentes" lists) and looks for the
process there, so the result can tell the user what the read did or would do:

- not in any pending queue, every queue read to its end: ``nenhuma``;
- in a pending queue: ``dada`` when documents were opened, ``pendente`` when none were (``intimacoes``, ``aviso``);
- the queues could not be read to their end: ``desconhecida`` when documents were opened (``motivo``), else
  ``nenhuma`` with an ``aviso``.

The timeline describes each intimação with its deadline (TJMG and JFRS, 27/11/2026): "Expedida/certificada a intimação
eletrônica Refer. ao Evento 40 ( AUTOR - name) Prazo: 10 dias Status:ABERTO Data inicial da contagem do prazo:
13/11/2026 00:00:00 Data final: 27/11/2026 23:59:59", or "Status:FECHADO ( 37 - Decorrido prazo )" once it closed,
followed by "Disponibilizado no DJEN - no dia ... - Refer. ao Evento: 41" and "Publicado no DJEN ...". ``deadlines``
reads them into the ``prazos`` of a process read. An intimação event whose status is neither ABERTO nor FECHADO
("AGUARD. ABERTURA") still waits to be opened (``awaiting_events``).

Which document a pending intimação covers: "Refer. ao Evento N" names the event whose documents it sends, but no live
read has shown a pending event yet (opening one is what must not happen), so ``EVENT_LINK_CONFIRMED`` stays off; on, the
block names the requested documents that belong to the pending event.
"""

import re

from jurisync.core.ciencia import GIVEN, NONE, PENDING, UNKNOWN, report

# The queues of ``client.QUEUE_KINDS`` that list citações/intimações still waiting for ciência.
PENDING_QUEUES = ("pending", "pending_urgent", "pending_agreement", "art334")
# Whether "Refer. ao Evento N" in an "AGUARD. ABERTURA" event is trusted to name the only documents the intimação
# covers. Not shown by any captured page: confirm live before turning it on.
EVENT_LINK_CONFIRMED = False

_AWAITING = re.compile(r"aguard\w*\.?\s*(?:a\s+)?abertura", re.I)
_REFERENCE = re.compile(r"refer\w*\.?\s*a?o?s?\s*eventos?\s*:?\s*((?:\d+\s*(?:,|\be\b)?\s*)+)", re.I)
_DEADLINE = re.compile(r"prazo\s*:?\s*(\d+)\s*dias?", re.I)
_DAYS = re.compile(r"(\d+)\s*dias?", re.I)
_STATUS = re.compile(r"status(?:\s+do\s+prazo)?\s*:\s*(.+?)(?=\s*\(|\s+data\b|$)", re.I)
_CLOSED_BY = re.compile(r"status(?:\s+do\s+prazo)?\s*:\s*fechado\s*\(\s*(\d+\s*-\s*[^)]*?)\s*\)", re.I)
_REFERRED = re.compile(r"refer\w*\.?\s*([^()]*?)\s*(?:\(|prazo\s*:|$)", re.I)
_PARTY = re.compile(r"\(\s*([A-ZÀ-Ú][A-ZÀ-Ú ]*?)\s+-\s")
_START = re.compile(r"data inicial da contagem do prazo\s*:\s*(\d{2}/\d{2}/\d{4})", re.I)
_END = re.compile(r"data final\s*:\s*(\d{2}/\d{2}/\d{4})", re.I)
_DJEN = re.compile(
    r"(disponibilizad|publicad)o no djen\s*-\s*no dia\s*(\d{2}/\d{2}/\d{4}).*?refer\w*\.?\s*a?o?s?\s*eventos?\s*:?"
    r"\s*((?:\d+\s*(?:,|\be\b)?\s*)+)",
    re.I,
)
QUEUE_FIELDS = ("queue", "deadlineEvent", "requestedAt", "deadlineStart", "deadlineEnd")
OPENED = {"ABERTO": "aberto", "FECHADO": "fechado"}


def _iso(value):
    day, month, year = value.split("/")
    return f"{year}-{month}-{day}"


def awaiting_publication(row):
    """A pending queue row that waits for its DJEN publication ("Aguarda publicação no DJEN" as the deadline start)."""
    return "publica" in str(row.get("deadlineStart") or "").casefold()


def _status(description, classes):
    found = _STATUS.search(description)
    if found:
        raw = " ".join(found.group(1).split()).upper()
        if raw in OPENED:
            return OPENED[raw]
        return "aguardando_abertura" if _AWAITING.search(raw) else raw.lower()
    if "infraEventoPrazoAberto" in classes:
        return "aberto"
    if "infraEventoPrazoFechado" in classes:
        return "fechado"
    return None


def deadlines(events):
    """The deadline of every intimação event of the timeline, oldest first, with its DJEN dates when shown.

    Each: eventId, dateTime, refersTo (the events it sends), party (the role it is addressed to), days, status
    (``aberto``, ``fechado``, ``aguardando_abertura`` or the portal's own word), closedBy (the event that closed it),
    start and end (ISO dates of the count), djenAvailable and djenPublished (ISO dates).
    """
    djen = {}
    for event in events or []:
        found = _DJEN.search(str(event.get("description") or ""))
        if found:
            kind = "djenAvailable" if found.group(1).lower().startswith("disponibiliz") else "djenPublished"
            for identifier in re.findall(r"\d+", found.group(3)):
                djen.setdefault(identifier, {})[kind] = _iso(found.group(2))
    result = []
    for event in events or []:
        description = " ".join(str(event.get("description") or "").split())
        classes = event.get("classes") or []
        status = _status(description, classes)
        if status is None or not (
            _DEADLINE.search(description) or _END.search(description) or _STATUS.search(description)
        ):
            continue
        referred = _REFERRED.search(description)
        days, party = _DEADLINE.search(description), _PARTY.search(description)
        start, end, closed = _START.search(description), _END.search(description), _CLOSED_BY.search(description)
        identifier = str(event.get("id") or "")
        result.append(
            {
                "eventId": identifier,
                "dateTime": event.get("dateTime", ""),
                "label": event.get("label", ""),
                "refersTo": re.findall(r"\d+", referred.group(1)) if referred else [],
                "party": party.group(1).strip() if party else None,
                "days": int(days.group(1)) if days else None,
                "status": status,
                "closedBy": " ".join(closed.group(1).split()) if closed else None,
                "start": _iso(start.group(1)) if start else None,
                "end": _iso(end.group(1)) if end else None,
                "djenAvailable": djen.get(identifier, {}).get("djenAvailable"),
                "djenPublished": djen.get(identifier, {}).get("djenPublished"),
            }
        )
    return sorted(result, key=lambda item: int(item["eventId"]) if item["eventId"].isdigit() else 0)


def awaiting_events(events):
    """Timeline events that announce an intimação still waiting to be opened, with the events they refer to: a
    deadline whose status is neither ABERTO nor FECHADO, or any event that says "aguardando abertura"."""
    waiting = {item["eventId"]: item for item in deadlines(events) if item["status"] not in OPENED.values()}
    found = []
    for event in events or []:
        description = str(event.get("description") or "")
        identifier = str(event.get("id") or "")
        if identifier not in waiting and not _AWAITING.search(description):
            continue
        reference = _REFERENCE.search(description)
        days = _DEADLINE.search(description)
        found.append(
            {
                "eventId": event.get("id"),
                "dateTime": event.get("dateTime", ""),
                "label": event.get("label", ""),
                "refersTo": re.findall(r"\d+", reference.group(1)) if reference else [],
                "deadlineDays": int(days.group(1)) if days else None,
            }
        )
    return found


def tied_documents(events, markers):
    """Ids of the documents the pending intimações cover, or ``None`` when that cannot be proven."""
    if not EVENT_LINK_CONFIRMED or not markers:
        return None
    by_id = {str(event.get("id")): event for event in events or [] if event.get("id")}
    tied = set()
    for marker in markers:
        covered = [str(marker["eventId"]), *marker["refersTo"]]
        if not marker["refersTo"] or any(identifier not in by_id for identifier in covered):
            return None
        for identifier in covered:
            tied.update(str(doc["id"]) for doc in by_id[identifier].get("documents", []) if doc.get("id"))
    return tied


def _describe(notice):
    parts = [notice.get("deadlineEvent") or "citação/intimação"]
    if notice.get("requestedAt"):
        parts.append(f"expedida em {notice['requestedAt']}")
    if notice.get("deadlineEnd"):
        start = f"de {notice['deadlineStart']} a " if notice.get("deadlineStart") else "até "
        parts.append(f"prazo {start}{notice['deadlineEnd']}")
    return ", ".join(parts)


def _describe_event(marker):
    text = f"evento {marker['eventId']} de {marker['dateTime']}"
    if marker["refersTo"]:
        text += f", referente ao evento {', '.join(marker['refersTo'])}"
    return text


def _deadline_days(notices, markers):
    """The deadline in days when every source that states one agrees, else ``None``."""
    days = {marker["deadlineDays"] for marker in markers if marker["deadlineDays"] is not None}
    for notice in notices:
        found = _DAYS.search(str(notice.get("deadlineEvent") or ""))
        if found:
            days.add(int(found.group(1)))
    return days.pop() if len(days) == 1 else None


def notice(process_number, document_ids, *, notices, problem, events, events_complete):
    """The ``ciencia`` block of a read that opened ``document_ids`` (``None``: no document was opened).

    ``notices`` are this process' rows in the pending queues, ``problem`` says why the queues could not be read to
    their end (``None`` when they were). Never refuses: the documents are read either way.
    """
    opened = document_ids is not None
    if not notices and problem:
        if not opened:
            block = report(
                NONE,
                f"Não foi possível verificar se o processo {process_number} tem intimação aguardando ciência; nenhum "
                "documento foi aberto. Abrir documentos dele pode registrar a ciência e iniciar o prazo.",
            )
        else:
            block = report(
                UNKNOWN,
                f"Não foi possível verificar se o processo {process_number} tinha intimação aguardando ciência; "
                "os documentos foram abertos e, se havia intimação pendente, a ciência pode ter sido registrada "
                "e o prazo iniciado. Informe o advogado.",
            )
        return {**block, "motivo": problem}
    if not notices:
        return report(NONE)
    markers = awaiting_events(events)
    days = _deadline_days(notices, markers)
    shown = "; ".join(_describe(row) for row in notices[:5])
    if markers:
        shown += "; na linha do tempo: " + "; ".join(_describe_event(marker) for marker in markers[:5])
    deadline = f"o prazo de {days} dias" if days else "o prazo"
    if all(awaiting_publication(row) for row in notices):
        shown += (
            "; o portal informa que ela aguarda a publicação no DJEN, que dá a ciência e abre o prazo mesmo sem "
            "abrir nada"
        )
    if opened:
        aviso = (
            f"Este processo ({process_number}) tinha intimação aguardando ciência ({shown}); abrir os documentos pode "
            f"ter registrado a ciência e iniciado {deadline}. Informe o advogado."
        )
    else:
        aviso = (
            f"Este processo ({process_number}) tem intimação aguardando ciência ({shown}); nenhum documento foi "
            f"aberto, mas abrir os documentos dele pode registrar a ciência e iniciar {deadline}."
        )
    block = report(
        GIVEN if opened else PENDING, aviso, [{key: row.get(key) for key in QUEUE_FIELDS} for row in notices]
    )
    if markers:
        block["eventos"] = markers
    tied = tied_documents(events, markers) if opened and events_complete else None
    if tied is not None:
        block["documentosDaIntimacao"] = sorted({str(identifier) for identifier in document_ids} & tied)
    return block
