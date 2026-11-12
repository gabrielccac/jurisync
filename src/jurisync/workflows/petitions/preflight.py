"""Check everything a petition needs before any drafting starts.

What is missing or in conflict is reported to the collaborator first, all at once. Drafting pauses there: the
collaborator supplies it or confirms going on, and then each open item is marked in the text and listed at the end of
the draft. Nothing is filled in by guesswork.
"""

from jurisync.workflows.petitions import calc
from jurisync.workflows.petitions.expressions import ExpressionError, evaluate
from jurisync.workflows.petitions.model import SHARED, AuthorCase, placeholders

CONDITIONAL = "condicional:"
ROMAN = ("I", "II", "III", "IV", "V", "VI", "VII", "VIII")
UNNUMBERED = {"enderecamento", "qualificacao", "fecho"}
EXTRA_FIELDS = {"autor.despesas"}
WHO = {
    "documento": "cliente ou triagem",
    "checklist": "cliente ou triagem",
    "resumo_do_caso": "triagem",
    "crm": "triagem",
    "escritorio": "cadastro do escritório",
    "calculado": "quem monta o dossiê",
    "decisao_advogada": "advogada",
}


def _source(spec):
    return str((spec or {}).get("fonte_provavel", "")).split(":")[0]


def selected_scenario(model, case, needed):
    for scenario in model.meta.get("cenarios", []):
        value, missing = evaluate(scenario["condicao"], case.get)
        if value is True:
            return scenario
        needed |= missing
    return None


def choose_text(block, case):
    """The first alternative whose condition holds replaces the text. Unknown conditions were asked in preflight."""
    for alternative in block.get("alternativas") or []:
        if evaluate(alternative.get("expressao"), case.get)[0] is True and alternative.get("expressao"):
            return alternative
    return block


def selected_blocks(model, scenario, case, needed):
    blocks, chosen = model.blocks, []
    for block_id in scenario["sequencia"]:
        block = blocks[block_id]
        value, missing = evaluate((block.get("quando_usar") or {}).get("expressao"), case.get)
        needed |= missing
        if value is True:
            chosen.append(block)
    return chosen


def _required_fields(model, case, needed):
    for name, spec in model.fields.items():
        rule = str(spec.get("obrigatorio", ""))
        if rule == "sempre":
            needed.add(name)
        elif rule.startswith(CONDITIONAL):
            try:
                value = evaluate(rule[len(CONDITIONAL) :], case.get)[0]
            except ExpressionError:
                continue
            if value is True:
                needed.add(name)


def _block_needs(model, blocks, case, needed):
    """Fixed text cannot go out with a hole. Drafted blocks use the facts available, so their needs come from the fields' rules."""
    for block in blocks:
        version = choose_text(block, case)
        if block.get("modo") in ("fixo", "campos"):
            needed |= {name for name in placeholders(version.get("texto")) if not model.genders.get(name)}
        for alternative in block.get("alternativas") or []:
            needed |= evaluate(alternative.get("expressao"), case.get)[1]


def _gender_needs(model, blocks, case, needed):
    for block in blocks:
        for name in placeholders(choose_text(block, case).get("texto")):
            needed |= set((model.genders.get(name) or {}).get("selecionado_por") or [])


def apply_numbering(model, blocks, case):
    """Cross-references come from the blocks actually selected, not from typed values."""
    numeral = subtitle = 0
    section = None
    for block in blocks:
        if block["secao"] != section:
            section, subtitle = block["secao"], 0
            if section not in UNNUMBERED:
                numeral += 1
        if block.get("imprime_titulo") and section not in UNNUMBERED:
            subtitle += 1
            if "exibicao" in block["id"] and block["secao"] == "direito":
                case.calculated.setdefault("item_exibicao", f"{ROMAN[numeral - 1]}.{subtitle}")


def _unknown_fields(model, dossier):
    known = set(model.fields)
    found = set()
    for part in SHARED + ("autor", "representante"):
        groups = [dossier.get(part)] + [author.get(part) for author in dossier.get("autores") or []]
        for group in groups:
            found |= {f"{part}.{key}" for key in group or {}}
    return sorted(found - known - EXTRA_FIELDS)


def check(model, dossier, author_index=0):
    case = AuthorCase(dossier, author_index)
    calc.apply(case)
    needed = set()
    scenario = selected_scenario(model, case, needed)
    _required_fields(model, case, needed)
    blocks = selected_blocks(model, scenario, case, needed) if scenario else []
    _block_needs(model, blocks, case, needed)
    _gender_needs(model, blocks, case, needed)
    apply_numbering(model, blocks, case)

    recorded = case.not_available()
    missing, not_held, alerts = [], [], []
    for name in sorted(needed):
        if case.get(name) is not None:
            continue
        spec = model.fields.get(name) or {}
        entry = recorded.get(name)
        if entry and entry.get("motivo") and entry.get("registrado_por"):
            not_held.append({"campo": name, "motivo": entry["motivo"], "registrado_por": entry["registrado_por"]})
            if spec.get("obrigatorio") == "sempre":
                alerts.append(
                    f"{spec.get('descricao', name)}: registrado como não disponível; sinalizar à coordenação."
                )
            continue
        source = _source(spec)
        missing.append(
            {
                "campo": name,
                "descricao": spec.get("descricao", "Campo sem descrição no modelo"),
                "fonte": spec.get("fonte_provavel", "não definida"),
                "quem_resolve": WHO.get(source, "colaborador"),
                "tipo": {"decisao_advogada": "decisao", "calculado": "calculo"}.get(source, "dado"),
            }
        )

    conflicts = [
        item for item in dossier.get("conflitos") or [] if not (item.get("resolvido") and item.get("resolvido_por"))
    ]
    strong = []
    for rule in model.rules:
        if evaluate(rule["se"], case.get)[0] is True:
            (strong if rule.get("tipo") == "aviso_forte" else alerts).append(rule["mensagem"])
    if not scenario and not missing:
        strong.append(
            "O caso não corresponde a nenhum cenário do modelo: indique em peca.cenario_referencia a referência mais próxima."
        )
    elif scenario and case.get("peca.cenario_referencia"):
        alerts.append(
            "Não há modelo exato para este caso: a minuta usa a referência mais próxima e a advogada deve conferir."
        )

    open_items = bool(missing or conflicts or strong)
    return {
        "autor": author_index,
        "cenario": (scenario or {}).get("id"),
        "completo": not open_items,
        "pode_seguir_com_pendencias": bool(scenario),
        "faltas": missing,
        "conflitos": conflicts,
        "avisos_fortes": strong,
        "nao_possui": not_held,
        "alertas": alerts,
        "blocos": [block["id"] for block in blocks],
        "campos_desconhecidos": _unknown_fields(model, dossier),
    }


def report_text(report):
    status = "COMPLETO" if report["completo"] else "PENDÊNCIAS: informe o que falta ou confirme para seguir"
    lines = [f"Conferência inicial do autor {report['autor'] + 1}: {status}"]
    groups = {
        "dado": "Dados e documentos faltando",
        "decisao": "Decisões da advogada",
        "calculo": "Cálculos a informar",
    }
    for kind, title in groups.items():
        items = [item for item in report["faltas"] if item["tipo"] == kind]
        if items:
            lines.append(f"\n{title}:")
            lines += [f"- {item['descricao']} ({item['campo']}). Resolve: {item['quem_resolve']}." for item in items]
    for key, title in (("conflitos", "Conflitos"), ("avisos_fortes", "Avisos fortes"), ("alertas", "Alertas")):
        if report[key]:
            lines.append(f"\n{title}:")
            lines += [f"- {item.get('descricao', item) if isinstance(item, dict) else item}" for item in report[key]]
    if report.get("campos_desconhecidos"):
        lines.append("\nCampos que o modelo não conhece (confira a grafia):")
        lines += [f"- {name}" for name in report["campos_desconhecidos"]]
    if report["nao_possui"]:
        lines.append("\nRegistrado como não disponível:")
        lines += [f"- {item['campo']}: {item['motivo']} ({item['registrado_por']})" for item in report["nao_possui"]]
    return "\n".join(lines)
