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
