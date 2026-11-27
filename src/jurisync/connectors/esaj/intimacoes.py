"""The ciência check that runs before a process's document viewer opens."""

import re

from curl_cffi.requests.exceptions import RequestException

from jurisync.connectors.esaj import common, dashboard
from jurisync.core import ciencia
from jurisync.core.session_pool import SessionExpired


def pending_intimation_numbers(session, portal):
    """Digits of every process with an intimação the panel still shows without ciência.

    The intimações carry only the process code; the panel resolves codes to numbers the way its own page does
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
            "An intimação without ciência has no process code, so it is unknown whether it is of this process",
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
            "The panel did not resolve every intimação without ciência to a process, so it is unknown whether one is "
            "of this process",
            "ciencia_unchecked",
        )
    return set(resolved.values())


CIENCIA_GIVEN = (
    "Este processo tinha intimação sem ciência no painel: abrir os documentos pode ter registrado a ciência e iniciado "
    "o prazo. Informe o advogado."
)


CIENCIA_UNKNOWN = (
    "Não foi possível verificar se o processo tinha intimação sem ciência: se tinha, abrir os documentos pode ter "
    "registrado a ciência e iniciado o prazo. Informe o advogado."
)


def check_ciencia(session, portal, number, acknowledge=False):
    """The ``ciencia`` block of a read of this process, checked before its document viewer opens.

    Unless the caller acknowledges, an intimação without ciência (``intimacao_pending``) or a panel list that does not
    say (``ciencia_unchecked``, or the list's own failure) refuses with nothing opened.
    """
    number = common.process_number(number, portal)
    try:
        pending = re.sub(r"\D", "", number) in pending_intimation_numbers(session, portal)
    except SessionExpired:
        raise
    except common.EsajBlocked as error:
        if acknowledge:
            return ciencia.report(ciencia.UNKNOWN, CIENCIA_UNKNOWN)
        raise common.EsajBlocked(
            f"ciência not checked: {error}. Nothing was opened. Opening the documents anyway may register a ciência "
            f"and start the deadline. {ciencia.AUTHORIZATION}.",
            error.reason,
        ) from None
    except (RuntimeError, RequestException):
        if acknowledge:
            return ciencia.report(ciencia.UNKNOWN, CIENCIA_UNKNOWN)
        raise
    if not pending:
        return ciencia.report(ciencia.NONE)
    if not acknowledge:
        raise common.EsajBlocked(
            "This process has an intimação without ciência in the panel. Nothing was opened. Opening its documents "
            f"may register the ciência and start the deadline. {ciencia.AUTHORIZATION}.",
            "intimacao_pending",
        )
    return ciencia.report(ciencia.GIVEN, CIENCIA_GIVEN)
