"""The panel's open-deadline intimações (``esaj_list_intimacoes``) and the ciência check that runs before a process's
document viewer opens.

What the panel calls intimações (``tarefas-adv/api/intimacoes``, the list the page labels "Manifestações / ciência")
are cases with an open deadline: the intimação was already received and the deadline runs. Each card's ``ciencia``
flag is the box the card closes with ("Fechar" on TJSP, "ciência com renúncia ao prazo" on other courts), set by an
irreversible ``PUT ciencia-intimacao`` this connector never sends; ``false`` only means the card is still open. TJSP
sends no deadline date. An intimação not yet received is not on this list: it shows up only on a movement's document,
where the portal asks for the receipt first (``receipt_required``).
"""

import re

from curl_cffi.requests.exceptions import RequestException

from jurisync.connectors.esaj import common, dashboard
from jurisync.core import ciencia, intimacao
from jurisync.core.session_pool import SessionExpired

INSTANCES = {"PG": "1g", "SG": "2g"}


def formatted_number(value):
    """A CNJ number with punctuation from the panel's ``nuProcesso`` (digits or already formatted); None otherwise."""
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) != 20:
        return None
    return f"{digits[:7]}-{digits[7:9]}.{digits[9:13]}.{digits[13]}.{digits[14:16]}.{digits[16:]}"


def process_numbers(session, portal, codes):
    """``{cdProcesso: CNJ number}`` for the codes the panel resolves (its own ``processos?cdsProcesso=`` lookup, 50 at
    a time); a code it does not resolve is left out."""
    resolved = {}
    codes = sorted(code for code in set(codes) if isinstance(code, str) and dashboard.PROCESS_CODE.fullmatch(code))
    for start in range(0, len(codes), 50):
        batch = codes[start : start + 50]
        for item in dashboard.dashboard_lookup(session, portal, [("cdsProcesso", code) for code in batch]):
            if isinstance(item, dict) and item.get("cdProcesso") in batch and formatted_number(item.get("nuProcesso")):
                resolved.setdefault(item["cdProcesso"], formatted_number(item["nuProcesso"]))
    return resolved


def intimacao_item(item, numbers):
    """One panel card as a ``list_intimacoes`` item.

    An open card is ``prazo_correndo`` (the intimação was received and its deadline runs; ``prazoFinal`` None: the
    portal sends no date). A closed card is ``sem_prazo``: closing it ("Fechar", or "ciência com renúncia ao prazo")
    gives up the deadline, so nothing is left to meet; the panel does not say whether the lawyer answered first, so it is
    never reported as ``respondido``.
    """
    from jurisync.connectors.esaj.parsers import public_json

    code = str(item.get("cdProcesso") or "") or None
    closed = item.get("ciencia") is True
    return {
        "processNumber": numbers.get(code),
        "cdProcesso": code,
        "instancia": INSTANCES.get(item.get("instancia"), item.get("instancia")),
        "titulo": public_json(item.get("titulo")),
        "descricao": public_json(item.get("descricao")),
        "dataMovimentacao": item.get("dataMovimentacao"),
        "cardFechado": closed,
        **intimacao.fields(intimacao.NO_DEADLINE if closed else intimacao.RUNNING),
    }


def list_intimacoes(session, portal, situacoes=None):
    """The panel's open-deadline intimações with the shared ``situacao``; without ``situacoes``, what needs attention."""
    from jurisync.core.responses import listing

    wanted = tuple(situacoes or intimacao.ATTENTION)
    if not set(wanted) <= set(intimacao.SITUATIONS):
        raise ValueError(f"situacao must be among {', '.join(intimacao.SITUATIONS)}")
    items = dashboard.intimations(session, portal)
    if not all(isinstance(item, dict) for item in items):
        raise common.EsajParseError("Unexpected intimações response")
    note = None
    try:
        numbers = process_numbers(session, portal, [item.get("cdProcesso") for item in items])
    except (common.EsajParseError, common.EsajBlocked) as error:
        numbers, note = {}, f"The panel did not resolve the process numbers ({error}); cdProcesso is kept."
    every = [intimacao_item(item, numbers) for item in items]
    chosen = [item for item in every if item["situacao"] in wanted]
    extra = {"situacoes": list(wanted), "cardsInPanel": len(every)}
    if note:
        extra["note"] = note
    return listing(chosen, key="items", total=len(chosen), more=None, **extra)


def open_deadline_numbers(session, portal):
    """Digits of every process with an open card in the panel's open-deadline list ("Manifestações / ciência").

    The cards carry only the process code; the panel resolves codes to numbers the way its own page does
    (``processos?cdsProcesso=...``). A code the panel does not resolve makes the answer unusable, so it raises.
    """
    items = dashboard.intimations(session, portal)
    if not all(isinstance(item, dict) for item in items):
        raise common.EsajParseError("Unexpected intimações response")
    codes = sorted({str(item.get("cdProcesso") or "") for item in items if item.get("ciencia") is not True})
    if not codes:
        return set()
    if "" in codes:
        raise common.EsajBlocked(
            "An open-deadline card of the panel has no process code, so it is unknown whether it is of this process",
            "ciencia_unchecked",
        )
    resolved = {}
    for start in range(0, len(codes), 50):
        batch = codes[start : start + 50]
        for item in dashboard.dashboard_lookup(session, portal, [("cdsProcesso", code) for code in batch]):
            if isinstance(item, dict) and item.get("cdProcesso") in batch:
                resolved[item["cdProcesso"]] = re.sub(r"\D", "", str(item.get("nuProcesso") or ""))
    if set(codes) - set(resolved) or not all(resolved.values()):
        raise common.EsajBlocked(
            "The panel did not resolve every open-deadline card to a process, so it is unknown whether one is of this "
            "process",
            "ciencia_unchecked",
        )
    return set(resolved.values())


# What a read reports. e-SAJ serves a document of an intimação not yet received only after the receipt is confirmed in
# the portal (the permission check refuses first: receipt_required), so a read here never registers that ciência.
OPEN_DEADLINE = (
    'Este processo tem prazo aberto no painel do advogado (lista "Manifestações / ciência"): a intimação já foi '
    "recebida e o prazo corre. Esta leitura não registrou ciência; se houver intimação ainda não recebida, o próprio "
    "portal pede a confirmação de recebimento antes de abrir o documento."
)


CIENCIA_UNKNOWN = (
    "Não foi possível consultar a lista de prazos abertos do painel antes desta leitura. O e-SAJ pede a confirmação de "
    "recebimento antes de abrir documento de intimação ainda não recebida, mas confira o processo no painel."
)


def check_ciencia(session, portal, number, acknowledge=False):
    """The ``ciencia`` block of a read of this process, checked before its document viewer opens.

    Unless the caller acknowledges, a process with an open-deadline card (``prazo_aberto``) or a panel list that does
    not say (``ciencia_unchecked``, or the list's own failure) refuses with nothing opened. The card is not a pending
    ciência (its intimação was received), so an acknowledged read reports ``nenhuma`` with a notice, never ``dada``;
    an intimação still waiting for its receipt is stopped later by the portal's own receipt check.
    """
    number = common.process_number(number, portal)
    try:
        open_deadline = re.sub(r"\D", "", number) in open_deadline_numbers(session, portal)
    except SessionExpired:
        raise
    except common.EsajBlocked as error:
        if acknowledge:
            return ciencia.report(ciencia.UNKNOWN, CIENCIA_UNKNOWN)
        raise common.EsajBlocked(
            f"The panel's open-deadline list was not checked: {error}. Nothing was opened. {ciencia.AUTHORIZATION}.",
            error.reason,
        ) from None
    except (RuntimeError, RequestException):
        if acknowledge:
            return ciencia.report(ciencia.UNKNOWN, CIENCIA_UNKNOWN)
        raise
    if not open_deadline:
        return ciencia.report(ciencia.NONE)
    if not acknowledge:
        raise common.EsajBlocked(
            "This process has an open deadline in the panel's 'Manifestações / ciência' list: its intimação was "
            "received and the deadline runs (this is not a pending ciência). Nothing was opened; reading is held "
            f"until the user knows. {ciencia.AUTHORIZATION}.",
            "prazo_aberto",
        )
    return ciencia.report(ciencia.NONE, OPEN_DEADLINE)
