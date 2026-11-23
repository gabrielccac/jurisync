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


def report(state, aviso="", intimacoes=()):
    if state not in STATES:
        raise ValueError(f"ciência state must be one of {', '.join(STATES)}")
    block = {"estado": state}
    if intimacoes:
        block["intimacoes"] = list(intimacoes)
    if aviso:
        block["aviso"] = aviso
    return block
