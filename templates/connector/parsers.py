"""Exemplo parsers: the portal's JSON in, the platform's dicts out. Pure functions, tested with fictitious fixtures."""


def intimacao(row):
    """One pending intimação of the portal's inbox."""
    return {
        "id": str(row["id"]),
        "processNumber": row["numeroProcesso"],
        "kind": row.get("tipo") or "intimação",
        "deadline": row.get("prazo"),
        "read": bool(row.get("lida")),
    }


def process(payload):
    """A process with its movements, newest first."""
    movements = sorted(payload.get("movimentos") or [], key=lambda item: item["data"], reverse=True)
    return {
        "number": payload["numero"],
        "class": payload.get("classe"),
        "court": payload.get("orgao"),
        "movements": [{"date": item["data"], "description": item["descricao"]} for item in movements],
    }
