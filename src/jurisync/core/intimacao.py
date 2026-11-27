"""The situation every ``<system>_list_intimacoes`` item reports, the same in PJe, Eproc, e-SAJ and Projudi, so a list
from any portal reads alike (an Eproc "intimação", a PJe "expediente" and a Projudi "intimação" are the same thing to
the lawyer: something sent to them that may start or already runs a deadline).

- ``aguardando_ciencia``: sent to the lawyer and not yet received; opening it registers the ciência (or the portal
  does after its tacit period) and starts the deadline;
- ``aguardando_publicacao``: waits for its DJEN/Diário publication, which gives the ciência; opening it changes nothing;
- ``prazo_correndo``: ciência registered (by the lawyer, the system or the publication) and the deadline runs;
- ``prazo_vencido``: the deadline ended and the portal records no answer;
- ``sem_prazo``: received, with no deadline to meet;
- ``respondido``: the portal records an answer;
- ``desconhecida``: the portal did not show which one.

Each item carries ``situacao`` and ``prazoFinal`` (ISO date, or None when the portal shows none), with
``prazoFinalTipo`` ``"portal"`` when the portal printed the date and ``"estimado"`` when it was computed or the portal
itself calls it an estimate. Without a filter, every ``list_intimacoes`` returns what needs the lawyer's attention:
``ATTENTION``.
"""

AWAITING_CIENCIA = "aguardando_ciencia"
AWAITING_PUBLICATION = "aguardando_publicacao"
RUNNING = "prazo_correndo"
EXPIRED = "prazo_vencido"
NO_DEADLINE = "sem_prazo"
ANSWERED = "respondido"
UNKNOWN = "desconhecida"
SITUATIONS = (AWAITING_CIENCIA, AWAITING_PUBLICATION, RUNNING, EXPIRED, NO_DEADLINE, ANSWERED, UNKNOWN)
# What a list returns without a filter: what still needs the lawyer.
ATTENTION = (AWAITING_CIENCIA, AWAITING_PUBLICATION, RUNNING)
DEADLINE_KINDS = ("portal", "estimado")
# For tool descriptions: one sentence every list_intimacoes can quote.
DESCRIPTION = (
    "Each item has situacao (aguardando_ciencia, aguardando_publicacao, prazo_correndo, prazo_vencido, sem_prazo, "
    "respondido, desconhecida) and prazoFinal (ISO date or null; prazoFinalTipo portal or estimado). Without a filter "
    "the list returns what needs attention: aguardando_ciencia, aguardando_publicacao and prazo_correndo."
)


def fields(situacao, prazo_final=None, tipo=None):
    """The shared keys of one item; ``prazo_final`` an ISO date string or None."""
    if situacao not in SITUATIONS:
        raise ValueError(f"situacao must be one of {', '.join(SITUATIONS)}")
    if prazo_final is not None and tipo not in DEADLINE_KINDS:
        raise ValueError(f"prazoFinalTipo must be one of {', '.join(DEADLINE_KINDS)}")
    return {"situacao": situacao, "prazoFinal": prazo_final, "prazoFinalTipo": tipo if prazo_final else None}
