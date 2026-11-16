"""STJ CPE JSON → the connector's output shapes, and the process identifiers it accepts.

The CPE answers JSON; these functions keep the fields a lawyer reads and drop the rest (party CPF/CNPJ, internal
sequence numbers that only feed other CPE calls, the registration data of the logged-in lawyer).
"""

import re

CNJ_DIGITS = re.compile(r"\d{20}")
CNJ_MASK = re.compile(r"(\d{7})-(\d{2})\.(\d{4})\.(\d)\.(\d{2})\.(\d{4})")
REGISTRO = re.compile(r"(\d{4})/?(\d{7})-?(\d)")
CLASS_NUMBER = re.compile(r"([A-Za-z][A-Za-z ]{0,15}?)\s*(\d{1,9})(?:\s*/\s*[A-Za-z]{2})?")
STATUS = {"active": "S", "closed": "N", "all": "T"}


def classify(value):
    """``("cnj", digits)``, ``("registro", 12 digits)`` or ``("classe", (sigla, numero))`` for a process reference.

    Accepts a CNJ number (masked or 20 digits), an STJ registration (``2026/0393422-3`` or 12 digits) or a class and
    number (``AREsp 3415877``, ``REsp 1234567/SP``).
    """
    text = (value or "").strip()
    compact = re.sub(r"[\s.\-/]", "", text)
    if CNJ_DIGITS.fullmatch(compact) or CNJ_MASK.fullmatch(text):
        return "cnj", re.sub(r"\D", "", text)
    if re.fullmatch(r"\d{12}", compact) or REGISTRO.fullmatch(text):
        return "registro", re.sub(r"\D", "", text)
    match = CLASS_NUMBER.fullmatch(text)
    if match:
        return "classe", (match.group(1).replace(" ", ""), match.group(2))
    raise ValueError(
        "process must be a CNJ number, an STJ registration (2026/0393422-3) or a class and number (AREsp 3415877)"
    )


def mask_cnj(digits):
    digits = re.sub(r"\D", "", digits or "")
    if len(digits) != 20:
        return digits or None
    return f"{digits[:7]}-{digits[7:9]}.{digits[9:13]}.{digits[13]}.{digits[14:16]}.{digits[16:]}"


def mask_registro(digits):
    digits = re.sub(r"\D", "", digits or "")
    return f"{digits[:4]}/{digits[4:11]}-{digits[11:]}" if len(digits) == 12 else digits


def require_registro(value):
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) != 12:
        raise ValueError("STJ registration must have 12 digits")
    return digits


def record(row):
    """One ``getNumeroRegistro*`` answer row."""
    return {
        "registro": mask_registro(row.get("numeroRegistro")),
        "process": f"{row.get('siglaClasse', '')} {row.get('numeroProcessoClasse', '')}".strip(),
        "uf": row.get("uf"),
        "cnj": mask_cnj(row.get("numeroUnico")),
    }


def _date(value):
    return (value or "")[:10] or None


def process_row(row):
    """One row of the "my processes" list."""
    return {
        "registro": mask_registro(row.get("numeroRegistro")),
        "process": row.get("processo"),
        "cnj": mask_cnj(row.get("numeroUnico")),
        "filedAt": _date(row.get("dataAutuacao")),
        "active": row.get("tramitando") == "S",
        "plaintiff": row.get("nomeParteAutora"),
        "defendant": row.get("nomeParteRe"),
        "lastPhaseAt": (row.get("dataUltimaFase") or "")[:16] or None,
        "lastPhase": row.get("textoFase"),
        "court": row.get("orgaoJulgador"),
        "rapporteur": row.get("ministroRelator"),
        "subject": row.get("assunto"),
        "secret": row.get("segredoJustica") == "S",
    }


def _name(value, key="nome"):
    return (value or {}).get(key) if isinstance(value, dict) else None


def rapporteurs(value):
    return [
        item["ministro"]["nomeMinistro"]
        for item in value or []
        if isinstance(item, dict) and isinstance(item.get("ministro"), dict) and item["ministro"].get("nomeMinistro")
    ]


def cover(data):
    """The process cover (``GET /processo/{registro}``)."""
    classe = data.get("classe") or {}
    origin = data.get("origem") or {}
    status = data.get("status") or {}
    return {
        "registro": data.get("numeroRegistroFormatado") or mask_registro(data.get("numeroRegistro")),
        "process": data.get("nomeCompleto") or data.get("nome"),
        "class": classe.get("nomeClasse"),
        "classAbbreviation": classe.get("sigla"),
        "cnj": data.get("numeroUnicoFormatado") or mask_cnj(data.get("numeroUnico")),
        "filedAt": data.get("dataAutuacao"),
        "protocolAt": data.get("dataRegistroProtocolo"),
        "origin": {
            "court": origin.get("nome"),
            "abbreviation": origin.get("sigla"),
            "place": data.get("nomeLocalOrigem"),
        },
        "court": _name(data.get("orgaoJulgador")) or _name(data.get("orgaoProcessante")),
        "rapporteurs": rapporteurs(data.get("ministroRelator")),
        "status": status.get("descricao"),
        "destination": _name(data.get("destino"), "descricao"),
        "subjects": [
            item.get("descricaoCompleta") or item.get("descAssunto")
            for item in data.get("processoAssunto") or []
            if isinstance(item, dict)
        ],
        "electronic": bool(data.get("processoEletronico")),
        "secret": bool(data.get("segredoJustica") or data.get("sigiloso")),
        "criminal": bool(data.get("criminal")),
        "freeJustice": bool(data.get("justicaGratuita")),
        "repetitive": bool(data.get("repetitivo")),
    }


def phase(row):
    return {
        "at": (row.get("dataHoraFase") or "")[:19] or None,
        "text": row.get("textoFase"),
        "summary": row.get("descricaoSimplificada"),
    }


def party_groups(rows):
    """Parties with their lawyers. The CPE lists the lawyers right after the party, or the run of parties, they act
    for; every party of that run gets them."""
    groups, run, after_lawyer = [], [], False
    for row in rows or []:
        if row.get("tipo") == "Advogado":
            if not run:
                run = [{"role": None, "side": None, "name": None, "lawyers": []}]
                groups.append(run[0])
            for party in run:
                party["lawyers"].append({"name": row.get("nome"), "oab": row.get("codigoOAB")})
            after_lawyer = True
            continue
        if after_lawyer:
            run, after_lawyer = [], False
        party = {
            "role": row.get("descricaoTipoParte") or row.get("siglaTipoParte"),
            "side": {"A": "active", "R": "passive"}.get(row.get("indicadorAutorReu")),
            "name": row.get("nome"),
            "lawyers": [],
        }
        groups.append(party)
        run.append(party)
    return groups


def pieces(rows):
    """The document index, one entry per piece (``sequencialPeca``). Linked pieces (``tipo`` 2) are skipped."""
    found = {}
    for row in rows or []:
        if row.get("tipo") not in (1, None) or row.get("sequencialPeca") in (None, 0):
            continue
        identifier = str(row["sequencialPeca"])
        entry = found.get(identifier)
        if entry is None:
            found[identifier] = {
                "id": identifier,
                "type": (row.get("descricaoTipoPeca") or "").strip() or None,
                "attachedAt": row.get("dataJuntada"),
                "pages": row.get("quantidadePagina") or 0,
                "source": row.get("indicadorOrigemPeca"),
                "page": row.get("numeroFolhaPeca"),
            }
        else:
            entry["pages"] += row.get("quantidadePagina") or 0
    return list(found.values())


def publication(row):
    return {
        "publishedAt": row.get("dataPublicacao"),
        "releasedAt": (row.get("dataDivulgacao") or "")[:10] or None,
        "edition": row.get("numeroEdicao"),
        "section": row.get("nomeCapitulo"),
        "subsection": row.get("nomeSubcapitulo"),
        "title": row.get("descDocumento"),
        "registro": mask_registro(row.get("numeroRegistro")),
        "process": f"{row.get('siglaClasse', '')} {row.get('numeroProcessoClasse', '')}".strip() or None,
    }
