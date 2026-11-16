"""Ciência notice for eproc document reads.

Opening the document of a citação/intimação whose deadline was not opened yet may make the lawyer count as notified,
which starts the deadline. Eproc documents are always read (owner's decision, 2026-11-09); before they are fetched the
client reads the lawyer's pending queues (the panel's "citações/intimações pendentes" lists) and looks for the process
there, so the result can tell the user what the read may have done:

- not in any pending queue, every queue read to its end: no notice;
- in a pending queue: ``ciencia = {"pendente": true, "intimacoes": [...], "aviso": ...}``;
- the queues could not be read to their end: ``ciencia = {"verificada": false, "motivo": ..., "aviso": ...}``.

Which document a pending intimação covers is not in the captured pages. The standard eproc timeline marks the
intimação event with "Status: AGUARD. ABERTURA" and "Refer. ao Evento N"; ``EVENT_LINK_CONFIRMED`` turns that reading
on (the notice then names the requested documents that belong to the pending event), and it stays off until it is
confirmed on a live portal.
"""

import re

# The queues of ``client.QUEUE_KINDS`` that list citações/intimações still waiting for ciência.
PENDING_QUEUES = ("pending", "pending_urgent", "pending_agreement", "art334")
# Whether "Refer. ao Evento N" in an "AGUARD. ABERTURA" event is trusted to name the only documents the intimação
# covers. Not shown by any captured page: confirm live before turning it on.
EVENT_LINK_CONFIRMED = False

_AWAITING = re.compile(r"aguard\w*\.?\s*(?:a\s+)?abertura", re.I)
_REFERENCE = re.compile(r"refer\w*\.?\s*a?o?s?\s*eventos?\s*:?\s*((?:\d+\s*(?:,|\be\b)?\s*)+)", re.I)
_DEADLINE = re.compile(r"prazo\s*:?\s*(\d+)\s*dias?", re.I)
_DAYS = re.compile(r"(\d+)\s*dias?", re.I)
QUEUE_FIELDS = ("queue", "deadlineEvent", "requestedAt", "deadlineStart", "deadlineEnd")


def awaiting_events(events):
    """Timeline events that announce an intimação still waiting to be opened, with the events they refer to."""
    found = []
    for event in events or []:
        description = str(event.get("description") or "")
        if not _AWAITING.search(description):
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
    """The keys to add to a read of ``document_ids``: ``{}`` when nothing was pending, else a ``ciencia`` block.

    ``notices`` are this process' rows in the pending queues, ``problem`` says why the queues could not be read to
    their end (``None`` when they were). Never refuses: the documents are read either way.
    """
    if not notices and problem:
        return {
            "ciencia": {
                "verificada": False,
                "motivo": problem,
                "aviso": (
                    f"Não foi possível verificar se o processo {process_number} tinha intimação aguardando ciência; "
                    "os documentos foram abertos e, se havia intimação pendente, a ciência pode ter sido registrada "
                    "e o prazo iniciado. Informe o advogado."
                ),
            }
        }
    if not notices:
        return {}
    markers = awaiting_events(events)
    days = _deadline_days(notices, markers)
    shown = "; ".join(_describe(row) for row in notices[:5])
    if markers:
        shown += "; na linha do tempo: " + "; ".join(_describe_event(marker) for marker in markers[:5])
    block = {
        "pendente": True,
        "intimacoes": [{key: row.get(key) for key in QUEUE_FIELDS} for row in notices],
    }
    if markers:
        block["eventos"] = markers
    tied = tied_documents(events, markers) if events_complete else None
    if tied is not None:
        block["documentosDaIntimacao"] = sorted({str(identifier) for identifier in document_ids} & tied)
    deadline = f"o prazo de {days} dias" if days else "o prazo"
    block["aviso"] = (
        f"Este processo ({process_number}) tinha intimação aguardando ciência ({shown}); abrir os documentos pode "
        f"ter registrado a ciência e iniciado {deadline}. Informe o advogado."
    )
    return {"ciencia": block}
