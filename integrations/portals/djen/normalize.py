"""Normalização DJEN → Comunicacao + hint preliminar de família POP.

Zero dependência de AdvBox/skill. Entrada: item bruto da API.
Saída: dict estável para o roteador da skill:
``route(comunicacao, perfil) -> {workflow_id, responsavel, prioridade, detalhar_pje}``.
"""

import re

OAB_VARIANTS = ("", "-A", "-B", "-E", "-N", "-O", "-S")

# Verbos padronizados do Judiciário → seção POP (preliminar; skill decide).
FAMILY_RULES = (
    ("laudo", re.compile(r"laudo\s*\(?s\)?\s*pericial|manifest\w*\s+sobre\s+o\(s\)\s*laudo", re.I), "POP-§2", False),
    ("contrarrazoes", re.compile(r"contrarraz", re.I), "POP-§5", False),
    ("calculos", re.compile(r"c[áa]lculos?", re.I), "POP-§4", False),
    ("sentenca", re.compile(r"senten[çc]a|julgo\s+procedente|julgo\s+improcedente|extinto\s+o\s+processo", re.I), "POP-§3", False),
    ("pericia_designada", re.compile(r"designo?\s+(a\s+)?per[íi]cia|per[íi]cia\s+(m[ée]dica|social)", re.I), "POP-§7/§8", False),
    ("acordo", re.compile(r"acordo|proposta\s+de\s+acordo", re.I), "POP-§?", False),
    ("emenda", re.compile(r"emendar?|emenda\s+[àa]\s+inicial|prazo\s+inicial|ato\s+ordinat[óo]rio", re.I), "POP-§1", False),
)

# Texto insuficiente → PJe secundário obrigatório.
NEEDS_PJE_RULES = (
    ("referencia_generica", re.compile(r"[úu]ltimo\s+ato|id\s+de\s+origem|documento\s+de\s+origem", re.I)),
    ("sigilo", re.compile(r"segredo\s+de\s+justi[çc]a|sigilo", re.I)),
    ("acordao_embargos_dcb", re.compile(r"ac[óo]rd[ãa]o|embargos\s+de\s+declara[çc][ãa]o|dcb|cessa[çc][ãa]o\s+de\s+benef[íi]cio", re.I)),
)


def normalize_oab_digits(numero_oab):
    """'14663-A' -> '14663'. Comparação sempre por dígitos."""
    return re.sub(r"\D", "", str(numero_oab or ""))


def is_pe_process(numero_processo_digits, nome_orgao):
    """PE dentro do TRF5 filtra no cliente: órgão 830x ou ' PE' no nome."""
    digits = re.sub(r"\D", "", str(numero_processo_digits or ""))
    orgao = str(nome_orgao or "")
    return bool(re.search(r"830\d", digits) or " PE" in orgao or "Pernambuco" in orgao)


def normalize_item(raw):
    """Item bruto → Comunicacao normalizada. Idempotência por ``id``."""
    advogados = []
    for entry in raw.get("destinatarioadvogados", []) or []:
        adv = entry.get("advogado", {}) or {}
        advogados.append({
            "nome": adv.get("nome"),
            "numero_oab": str(adv.get("numero_oab") or ""),
            "numero_digits": normalize_oab_digits(adv.get("numero_oab")),
            "uf_oab": adv.get("uf_oab"),
        })
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
        "link": raw.get("link"),
        "hash": raw.get("hash"),
        "ativo": bool(raw.get("ativo", True)),
        "data_cancelamento": raw.get("data_cancelamento"),
        "status": raw.get("status"),
        "destinatarios": [
            {"nome": d.get("nome"), "polo": d.get("polo")}
            for d in raw.get("destinatarios", []) or []
        ],
        "advogados": advogados,
        "is_pe": is_pe_process(numero_digits, raw.get("nomeOrgao")),
    }


def classify_family(texto):
    """(familia, secao_pop, detalhar_pje, motivo). Heurística preliminar."""
    text = texto or ""
    for name, pattern, secao, detalhar in FAMILY_RULES:
        if pattern.search(text):
            return name, secao, detalhar, ""
    for name, pattern in NEEDS_PJE_RULES:
        if pattern.search(text):
            return "indefinido", "POP-?", True, name
    if len(text.strip()) < 200:
        return "indefinido", "POP-?", True, "texto_curto"
    return "indefinido", "POP-?", True, "sem_verbo_mapeado"


def route_hint(comunicacao):
    """Hint serializável para a skill decidir a cadeia POP completa."""
    familia, secao, detalhar_pje, motivo = classify_family(comunicacao.get("texto", ""))
    return {
        "comunicacao_id": comunicacao.get("id"),
        "numero_processo": comunicacao.get("numero_processo_mascara"),
        "familia": familia,
        "secao_pop": secao,
        "detalhar_pje": detalhar_pje or not comunicacao.get("ativo", True)
        or bool(comunicacao.get("data_cancelamento")),
        "motivo_detalhe": motivo or ("cancelada" if comunicacao.get("data_cancelamento") else ""),
        "is_pe": comunicacao.get("is_pe", False),
    }
