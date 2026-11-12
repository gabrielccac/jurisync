"""Agent-sized views of compact DJEN items: one line per item, a summary by category, and result checks.

The JSON view stays the default for skills that route on fields; these views are for reading and triage.
The category is a reading aid from the text, not the legal classification the office's skill makes.
"""

import re
from collections import Counter

SEALED = "documento protegido por sigilo"

# Where the operative part starts: rulings first, then the usual intimation openings.
RULING = re.compile(
    r"\b(julgo\s|julgar\s|ante o exposto|isto posto|diante do exposto|pelo exposto|nego provimento|dou provimento|"
    r"negar provimento|dar provimento|dar parcial provimento|n[ãa]o conhecer|extingo|homologo|"
    r"indefiro a (?:petição )?inicial|acordam|negar-lhe provimento|dar-lhe provimento|provimento ao recurso|"
    r"recurso (?:in|des|im)?provido)",
    re.IGNORECASE,
)
OPENING = re.compile(
    r"\b(data da per[íi]cia|ficam?\s|fica a parte|fica v\.|intime-se|intimad[oa]s?\b|determino|defiro|indefiro|"
    r"designo|cite-se|em complementa|considerando|trata-se|de ordem|ato ordinat)",
    re.IGNORECASE,
)
# Opening formulas that say nothing about the content.
FILLER = re.compile(
    r"^(?:de ordem d[oa][^,:]{0,90}[,:]\s*|ato ordinat[óo]rio\s*(?:\([^)]{0,60}\))?\s*|"
    r"(?:e )?(?:em conformidade com|com amparo n)[^;]{0,160}?\bN?CPC(?:/\d+)?\b[,.]?\s*)+",
    re.IGNORECASE,
)
# The case header that some courts put before the text: skip to just after the defendant.
HEADER_END = re.compile(r"(?:REU|RÉU|POLO PASSIVO)\s*:?[^:]{0,80}?(?:INSS|SOCIAL)\b\s*", re.IGNORECASE)
LEADING_LABEL = re.compile(r"^[-–\s]*(?:INSS\b)?\s*(?:DESPACHO|DECIS[ÃA]O|INTIMA[ÇC][ÃA]O|SENTEN[ÇC]A)?\s*")
PRAZO = re.compile(
    r"prazo (?:de |legal de )?(\d{1,3})\s*(?:\([^)]{1,20}\)\s*)?(dias? [úu]teis|dias?|horas?)", re.IGNORECASE
)
PERICIA_WHEN = re.compile(
    r"data da per[íi]cia:\s*(\d{2}/\d{2}/\d{4}).{0,40}?hor[áa]rio da per[íi]cia:\s*(\d{1,2}:\d{2})",
    re.IGNORECASE | re.DOTALL,
)

# First match wins. (name, kind of document or None, pattern in the text or None)
CATEGORIES = (
    ("sigilo", None, re.compile(SEALED, re.IGNORECASE)),
    ("sentenca", "sentença", None),
    ("acordao", "acórdão", None),
    ("decisao", "decisão", None),
    ("pauta", "intimação de pauta", None),
    ("emenda", "intimação para emendar", None),
    ("emenda", None, re.compile(r"emend|sob pena de indeferimento", re.IGNORECASE)),
    ("pericia_cancelada", None, re.compile(r"cancelamento da per[íi]cia", re.IGNORECASE)),
    ("pericia", None, PERICIA_WHEN),
    ("laudo", None, re.compile(r"manifest\w*[^.]{0,40}laudo", re.IGNORECASE)),
    ("pericia", None, re.compile(r"per[íi]cia", re.IGNORECASE)),
    ("rpv", None, re.compile(r"\brpv\b|requisit[óo]rio|precat[óo]rio", re.IGNORECASE)),
    ("acordo", None, re.compile(r"acordo proposto|proposta de acordo|aceita o acordo", re.IGNORECASE)),
    ("audiencia", None, re.compile(r"audi[êe]ncia", re.IGNORECASE)),
    ("despacho", "despacho", None),
)
CATEGORY_NAMES = tuple(dict.fromkeys([name for name, _, _ in CATEGORIES] + ["outro"]))
# Categories where the cut text is not enough to conclude or act: read the full text first.
NEEDS_DETAIL = {"sentenca", "acordao", "emenda", "decisao", "outro"}
# Categories that usually need nobody to act (left out by so_acao). Unknown and sealed items stay in.
INFORMATIVE = {"pericia_cancelada"}
# Categories whose text adds nothing: shown without the núcleo.
NO_CORE = {"sigilo", "pericia_cancelada"}


def categoria(item):
    kind = (item.get("tipo_documento") or "").strip().lower()
    text = item.get("texto") or ""
    for name, doc_kind, pattern in CATEGORIES:
        if (doc_kind and kind == doc_kind) or (pattern and pattern.search(text)):
            return name
    return "outro"


def nucleo(item, limit=140):
    """The operative sentence of the text, without the court/case header; perícia date and time first."""
    text = re.sub(r"\s+", " ", item.get("texto") or "").strip()
    when = PERICIA_WHEN.search(text)
    prazo = PRAZO.search(text)
    prefix = f"perícia {when.group(1)} {when.group(2)} · " if when else ""
    if prazo:
        prefix += f"prazo citado {prazo.group(1)} {prazo.group(2).lower()} · "
    kind = (item.get("tipo_documento") or "").lower()
    match = None
    if kind in ("sentença", "acórdão", "decisão"):
        rulings = list(RULING.finditer(text))
        if rulings:  # the decision is usually the last ruling formula (acórdãos open with the ementa)
            match = rulings[-1] if kind == "acórdão" else rulings[0]
    if not match:
        match = OPENING.search(text)
    if match:
        core = text[match.start() :]
    else:
        header = HEADER_END.search(text[:700])
        core = text[header.end() :] if header else text
    core = FILLER.sub("", core).strip()
    core = LEADING_LABEL.sub("", core).strip()
    core = prefix + (core[:1].upper() + core[1:])
    return core[:limit] + "…" if limit and len(core) > limit else core


def dedupe(items):
    """Drop repeated items (same id, else same hash), keeping the first. Returns (items, removed)."""
    seen, kept = set(), []
    for item in items:
        key = item.get("id") or item.get("hash")
        if key in seen:
            continue
        seen.add(key)
        kept.append(item)
    return kept, len(items) - len(kept)


def filter_items(items, tipos=None, so_acao=False, limite=0, categorias=None):
    wanted = {t.strip().lower() for t in tipos or [] if t.strip()}
    cats = {c.strip().lower() for c in categorias or [] if c.strip()}
    out = [
        i
        for i in items
        if (not wanted or (i.get("tipo_documento") or "").strip().lower() in wanted)
        and (not cats or categoria(i) in cats)
        and (not so_acao or categoria(i) not in INFORMATIVE)
    ]
    return out[:limite] if limite else out


def short_orgao(name):
    name = re.sub(r"\bVara Federal\b", "VF", name or "")
    return re.sub(r"\bRelatoria da\b", "Rel.", name)


def _cut(text, limit):
    text = re.sub(r"\s+", " ", text or "").strip()
    return text[:limit] + "…" if limit and len(text) > limit else text


def _portal(item):
    portal = item.get("portal") or {}
    return portal.get("portal") or portal.get("system") or "?"


def _shared(items):
    shared = {}
    for key, label in (("data_disponibilizacao", "data"), ("sigla_tribunal", "tribunal")):
        values = {i.get(key) for i in items}
        if items and len(values) == 1:
            shared[key] = (label, values.pop())
    return shared


def _row(item, shared, texto_max, with_kind=True):
    parts = [item.get(key) or "" for key in ("data_disponibilizacao", "sigla_tribunal") if key not in shared]
    cancelled = item.get("data_cancelamento") or item.get("ativo") is False
    flag = f"[CANCELADA {item.get('data_cancelamento') or ''}] ".replace(" ]", "]") if cancelled else ""
    parts.append(flag + (item.get("numero_processo_mascara") or ""))
    if with_kind:
        parts.append(item.get("tipo_documento") or "")
    parts += [
        _portal(item),
        short_orgao(item.get("orgao")),
        _cut(", ".join(d.get("nome", "") for d in item.get("destinatarios") or []), 50),
        item.get("hash") or "",
    ]
    if texto_max:
        parts.append(nucleo(item, texto_max))
    return " | ".join(parts)


def _header(shared, texto_max, with_kind=True):
    out = []
    if shared:
        out.append("# " + " ".join(f"{label}={value}" for label, value in shared.values()))
    cols = ["processo", *(["tipo"] if with_kind else []), "portal", "órgão", "destinatários", "hash"]
    out.append("# " + " | ".join(cols + (["núcleo do teor"] if texto_max else [])))
    return out


def lines(items, texto_max=140):
    """Header with the values every item shares, then one line per item without them."""
    shared = _shared(items)
    return "\n".join(_header(shared, texto_max) + [_row(i, shared, texto_max) for i in items])


def summary(items, texto_max=110, counts_from=None, hint="--ler CATEGORIA ou djen-cli certidao HASH"):
    """Counts by category, then the items grouped by category; a núcleo shared by several items is printed once.

    ``counts_from`` is the whole filtered set when ``items`` is only the part that fits on screen.
    """
    pool = counts_from if counts_from is not None else items
    counts = Counter(categoria(i) for i in pool)
    out = ["# por categoria: " + ", ".join(f"{k}={n}" for k, n in counts.most_common())]
    acting = sum(n for k, n in counts.items() if k not in INFORMATIVE)
    out.append(f"# pedem leitura: {acting} de {len(pool)}")
    groups = {}
    for item in items:
        groups.setdefault(categoria(item), []).append(item)
    shared = _shared(items)
    out += _header(shared, texto_max, with_kind=False)
    order = sorted(groups.items(), key=lambda kv: (kv[0] in NO_CORE or kv[0] == "outro", -len(kv[1])))
    for name, group in order:
        out.append(f"## {name} ({len(group)})")
        if name in NO_CORE:
            out += [_row(i, shared, 0) for i in group]
            continue
        by_core = {}
        for item in group:
            by_core.setdefault(nucleo(item, texto_max), []).append(item)
        for core, same in by_core.items():
            if len(same) > 1:
                out.append(f"### {core}")
                out += [_row(i, shared, 0, with_kind=False) for i in same]
            else:
                out.append(_row(same[0], shared, texto_max, with_kind=False))
    detail = [f"{k}={n}" for k, n in counts.most_common() if k in NEEDS_DETAIL]
    if detail:
        out.append(f"# ler a ordem/dispositivo antes de concluir: {', '.join(detail)} ({hint})")
    return "\n".join(out)


def checks(declared, fetched, removed, pages, complete):
    """Compare what the source declared with what came back.

    ``complete`` is True when the pages read cover the declared total; then the count must match it.
    """
    obtained = len(fetched) + removed
    warnings = []
    if complete and obtained != declared:
        warnings.append(f"total declarado {declared} diferente do obtido {obtained}")
    if not complete:
        warnings.append(f"faltam {max(declared - obtained, 0)} itens em outras páginas")
    if removed:
        warnings.append(f"{removed} repetidos removidos")
    dates = sorted({i.get("data_disponibilizacao") for i in fetched if i.get("data_disponibilizacao")})
    return {
        "status": "ok" if complete and obtained == declared else "parcial",
        "declarado": declared,
        "obtido": obtained,
        "unicos": len(fetched),
        "repetidos": removed,
        "paginas": pages,
        "completo": complete,
        "datas": [dates[0], dates[-1]] if dates else [],
        "avisos": warnings,
    }


def checks_line(c):
    datas = "..".join(c["datas"]) if c["datas"] else "-"
    head = (
        f"status={c['status']} declarado={c['declarado']} obtido={c['obtido']} unicos={c['unicos']} "
        f"paginas={c['paginas']} datas={datas}"
    )
    return "\n".join([head, *(f"aviso: {w}" for w in c["avisos"])])
