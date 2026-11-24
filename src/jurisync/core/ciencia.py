"""The ``ciencia`` block every tool that opens an act or a document returns, in every system.

Reading may register the lawyer's ciência (the notice that starts a deadline). That is accepted when it is the moment
to read, but the user must always know what the read did:

- ``nenhuma``: nothing opened was a pending citação/intimação;
- ``dada``: this read opened a pending one, so the portal registered (or may have registered) the ciência now;
- ``pendente``: a pending one was found and not opened; reading it will register the ciência;
- ``desconhecida``: whether something pending was opened could not be checked.

``intimacoes`` lists the pending items involved when known, and ``aviso`` is the Portuguese text to relay.
"""

NONE, GIVEN, PENDING, UNKNOWN = "nenhuma", "dada", "pendente", "desconhecida"
STATES = (NONE, GIVEN, PENDING, UNKNOWN)

# When an agent may pass acknowledgeCiencia=true: the user decides, now or in advance.
AUTHORIZATION = (
    "Ask the user first, unless they already authorized it in advance (a standing instruction such as a scheduled "
    "task's prompt or 'pode dar ciência nas leituras'); then repeat the call with acknowledgeCiencia=true without "
    "asking again, and still relay the ciencia block's aviso to the user and note it in the task"
)
ACKNOWLEDGE = (
    "Leave empty. true opens what may register a pending ciência and start the deadline; only with the user's "
    "authorization, given now or in advance (see the tool description)."
)


def report(state, aviso="", intimacoes=()):
    if state not in STATES:
        raise ValueError(f"ciência state must be one of {', '.join(STATES)}")
    block = {"estado": state}
    if intimacoes:
        block["intimacoes"] = list(intimacoes)
    if aviso:
        block["aviso"] = aviso
    return block
