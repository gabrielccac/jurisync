"""Normalize public DJEN records; client-specific routing belongs to the skill."""

import html
import re


def strip_html(texto):
    """STJ-style HTML tables → plain text. Plain-text records pass through."""
    text = html.unescape(re.sub(r"<[^>]+>", " ", str(texto or "")))
    return re.sub(r"\s+", " ", text).strip()


def normalize_oab_digits(numero_oab):
    """'14663-A' -> '14663'. Comparação sempre por dígitos."""
    return re.sub(r"\D", "", str(numero_oab or ""))


def normalize_item(raw):
    """Item bruto → Comunicacao normalizada. Idempotência por ``id``."""
    advogados = []
    for entry in raw.get("destinatarioadvogados", []) or []:
        adv = entry.get("advogado", {}) or {}
        advogados.append(
            {
                "nome": adv.get("nome"),
                "numero_oab": str(adv.get("numero_oab") or ""),
                "numero_digits": normalize_oab_digits(adv.get("numero_oab")),
                "uf_oab": adv.get("uf_oab"),
            }
        )
    numero_digits = re.sub(r"\D", "", str(raw.get("numero_processo") or ""))
    return {
        "id": raw.get("id"),
        "numero_processo": numero_digits,
        "numero_processo_mascara": raw.get("numeroprocessocommascara"),
        "sigla_tribunal": raw.get("siglaTribunal"),
        "orgao": {"id": raw.get("idOrgao"), "nome": raw.get("nomeOrgao")},
        "classe": {"codigo": str(raw.get("codigoClasse") or ""), "nome": raw.get("nomeClasse")},
        "tipo_comunicacao": raw.get("tipoComunicacao"),
        "tipo_documento": raw.get("tipoDocumento"),
        "data_disponibilizacao": raw.get("data_disponibilizacao") or raw.get("datadisponibilizacao"),
        "texto": raw.get("texto") or "",
        "texto_limpo": strip_html(raw.get("texto") or ""),
        "link": raw.get("link"),
        "hash": raw.get("hash"),
        "ativo": bool(raw.get("ativo", True)),
        "data_cancelamento": raw.get("data_cancelamento"),
        "status": raw.get("status"),
        "destinatarios": [{"nome": d.get("nome"), "polo": d.get("polo")} for d in raw.get("destinatarios", []) or []],
        "advogados": advogados,
    }


COMPACT_KEYS = (
    "id",
    "numero_processo_mascara",
    "sigla_tribunal",
    "tipo_comunicacao",
    "tipo_documento",
    "data_disponibilizacao",
    "hash",
    "ativo",
    "data_cancelamento",
)


def compact_item(item, text_limit=0):
    """A routing-sized view of a normalized item: one copy of the text, lawyers and recipients trimmed.

    ``texto`` and ``texto_limpo`` are the same text for plain-text records, so the compact view keeps the clean one
    as ``texto``. ``text_limit`` > 0 cuts it and sets ``texto_truncado``; the full text stays reachable through
    ``djen_get_certidao_text``.
    """
    compact = {key: item.get(key) for key in COMPACT_KEYS}
    compact["orgao"] = (item.get("orgao") or {}).get("nome")
    compact["classe"] = (item.get("classe") or {}).get("nome")
    compact["destinatarios"] = item.get("destinatarios") or []
    compact["advogados"] = [
        {"nome": a.get("nome"), "oab": f"{a.get('numero_digits')}/{a.get('uf_oab')}"}
        for a in item.get("advogados") or []
    ]
    text = item.get("texto_limpo") or ""
    compact["texto_chars"] = len(text)
    if text_limit and len(text) > text_limit:
        compact["texto"] = text[:text_limit]
        compact["texto_truncado"] = True
    else:
        compact["texto"] = text
        compact["texto_truncado"] = False
    return compact
