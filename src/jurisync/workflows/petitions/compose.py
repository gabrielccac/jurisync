"""Turn a checked dossier into a drafting plan, then audit the drafted petition before rendering it."""

import json
import re
import unicodedata

from jurisync.workflows.petitions import calc, preflight
from jurisync.workflows.petitions.model import PLACEHOLDER, AuthorCase

GENDER_KEYS = {
    ("masculino", "singular"): ("autor", "masculino"),
    ("feminino", "singular"): ("autora", "feminino"),
    ("masculino", "plural"): ("autores", "masculino_plural"),
    ("feminino", "plural"): ("autoras", "feminino_plural"),
}
PENDING = re.compile(r"\[PENDENTE:[^\]]*\]")
LEFTOVER = re.compile(r"\{\{|\}\}|\[PREENCHER|\[trecho|\[Variante|\[Imagem", re.I)
NUMBER = r"(?:n[ºo°.]*\s*)?\d[\d.\s/-]*\d"
CITATION = re.compile(
    rf"\b(?:REsp|AREsp|AgInt|ARE|RE|HC|Tema|S[úu]mula|Apela[çc][ãa]o(?:\s+C[íi]vel)?|AC|Processo)\s*(?:n[ºo°.]*\s*)?:?\s*{NUMBER}"
    r"|\b\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}\b",
    re.I,
)
ARTICLE = re.compile(r"\barts?\.?\s*(\d[\d.]*)", re.I)
MONEY = re.compile(r"R\$\s*:?\s*(\d[\d.]*,\d{2})")
DATE = re.compile(r"\b\d{2}/\d{2}/\d{4}\b")
WRONG_GENDER = {
    "masculino": re.compile(r"\b(?:a|da|à|pela) Autora\b|\bA Autora\b"),
    "feminino": re.compile(r"\b(?:o|do|ao|pelo) Autor\b|\bO Autor\b"),
}
DRAFTED = ("adaptar_ao_caso", "gerar_narrativa")


class NotReady(ValueError):
    """The preflight found something open; drafting must not start."""


def _gender_value(spec, case):
    selectors = spec.get("selecionado_por") or []
    gender = case.get(selectors[0]) if selectors else None
    number = (case.get(selectors[1]) if len(selectors) > 1 else None) or "singular"
    keys = GENDER_KEYS.get((gender, number))
    if not keys:
        return None
    return spec.get(keys[0], spec.get(keys[1]))


def _image_marker(value):
    if isinstance(value, dict):
        return f"[[IMG|{value.get('arquivo', '')}|{value.get('legenda', '')}]]"
    return f"[[IMG|{value}|]]"


def _pending(name, model):
    description = (model.fields.get(name) or {}).get("descricao") or name
    return f"[PENDENTE: {description}]"


def fill(text, model, case, mark_missing=False):
    def replace(match):
        name = match.group(1).strip()
        spec = model.genders.get(name)
        value = _gender_value(spec, case) if spec else case.get(name)
        if value is None:
            return _pending(name, model) if mark_missing else match.group(0)
        return _image_marker(value) if name.startswith("imagem.") else str(value)

    return PLACEHOLDER.sub(replace, text or "")


def plan(model, dossier, author_index=0, *, confirmed_by=None):
    """The drafting plan. With open items it pauses unless someone confirms going on with them marked in the draft."""
    report = preflight.check(model, dossier, author_index)
    if not report["pode_seguir_com_pendencias"]:
        raise NotReady(preflight.report_text(report))
    if not report["completo"] and not confirmed_by:
        raise NotReady(preflight.report_text(report))
    case = AuthorCase(dossier, author_index)
    calc.apply(case)
    preflight.apply_numbering(model, [model.blocks[block_id] for block_id in report["blocos"]], case)
    items = []
    for block_id in report["blocos"]:
        block = model.blocks[block_id]
        version = preflight.choose_text(block, case)
        locked = block["modo"] not in DRAFTED
        item = {
            "id": block_id,
            "secao": block["secao"],
            "modo": block["modo"],
            "titulo": fill(block.get("titulo"), model, case, mark_missing=True),
            "imprime_titulo": bool(block.get("imprime_titulo")),
            "status": block.get("status"),
            "citacoes": version.get("citacoes") or block.get("citacoes") or [],
            "texto": fill(version.get("texto"), model, case, mark_missing=locked),
        }
        if not locked:
            facts = version.get("fatos_necessarios") or block.get("fatos_necessarios") or []
            item["instrucao"] = block.get("instrucao_de_adaptacao")
            item["estrutura"] = block.get("estrutura") or []
            item["fatos"] = {name: case.get(name) for name in facts}
            item["fatos_ausentes"] = [
                (model.fields.get(name) or {}).get("descricao") or name for name in facts if case.get(name) is None
            ]
        items.append(item)
    return {
        "autor": author_index,
        "cenario": report["cenario"],
        "alertas": report["alertas"],
        "nao_possui": report["nao_possui"],
        "pendencias": {
            "faltas": report["faltas"],
            "conflitos": report["conflitos"],
            "avisos_fortes": report["avisos_fortes"],
            "confirmado_por": confirmed_by if not report["completo"] else None,
        },
        "itens": items,
    }


def _digits(text):
    return re.sub(r"\D", "", text)


def _allowed_sources(model, dossier, case, items, new_citations=()):
    """What a drafted block may cite or state: the model's texts, the plan and the case's own data."""
    legal = [citation.get("referencia", "") for citation in model.meta.get("citacoes", [])]
    for item in items:
        block = model.blocks[item["id"]]
        legal.append(block.get("texto") or "")
        legal += [alternative.get("texto") or "" for alternative in block.get("alternativas") or []]
    legal += [item.get("referencia", "") for item in new_citations if item.get("fonte")]
    legal = "\n".join(legal)
    facts = "\n".join([json.dumps(dossier, ensure_ascii=False), json.dumps(case.calculated, ensure_ascii=False)])
    facts += "\n" + "\n".join(item["texto"] for item in items)
    facts += "\n" + "\n".join(
        f"{day}/{month}/{year}" for year, month, day in re.findall(r"(\d{4})-(\d{2})-(\d{2})", facts)
    )
    return {
        "citation_digits": _digits_runs(legal),
        "articles": {m.rstrip(".") for m in ARTICLE.findall(legal)},
        "facts": facts,
    }


def _digits_runs(text):
    runs = set()
    for match in re.finditer(r"\d[\d.\s/-]*\d|\d", text):
        runs.add(_digits(match.group(0)))
    return runs


def assemble(model, dossier, drafting_plan, drafted=None):
    """Apply the drafted texts and audit the result.

    Besides ``{block id: text}``, the draft may carry ``_omitidos`` ({id: reason}), ``_extras``
    ([{secao, titulo, texto, depois_de}]), ``_ajustes_fixos`` ({id: {texto, motivo, autorizado_por}}) and
    ``_citacoes_novas`` ([{referencia, fonte}]). Each one needs its justification and goes to the review list.
    """
    drafted = dict(drafted or {})
    omitted = drafted.pop("_omitidos", {})
    extras = drafted.pop("_extras", [])
    adjustments = drafted.pop("_ajustes_fixos", {})
    new_citations = drafted.pop("_citacoes_novas", [])
    case = AuthorCase(dossier, drafting_plan["autor"])
    calc.apply(case)
    problems, final = [], []
    allowed = _allowed_sources(model, dossier, case, drafting_plan["itens"], new_citations)
    problems += [
        f"Citação nova sem fonte: {item.get('referencia', '')}." for item in new_citations if not item.get("fonte")
    ]
    for item in drafting_plan["itens"]:
        identifier = item["id"]
        if item["modo"] in DRAFTED:
            if identifier in omitted:
                if not str(omitted[identifier]).strip():
                    problems.append(f"{identifier}: bloco omitido sem motivo.")
                continue
            text = drafted.get(identifier)
            if not (text or "").strip():
                problems.append(f"{identifier}: bloco sem redação.")
                text = ""
        else:
            if identifier in omitted:
                problems.append(f"{identifier}: bloco padrão do escritório não pode ser omitido na redação.")
            adjustment = adjustments.get(identifier)
            if identifier in drafted and drafted[identifier] != item["texto"]:
                problems.append(f"{identifier}: bloco padrão do escritório só muda com ajuste autorizado.")
            text = item["texto"]
            if adjustment:
                if not (adjustment.get("motivo") and adjustment.get("autorizado_por")):
                    problems.append(f"{identifier}: ajuste sem motivo ou sem quem autorizou.")
                text = adjustment.get("texto") or text
        final.append({**item, "texto": text})
        mode = "adaptar_ao_caso" if identifier in adjustments else item["modo"]
        problems += _audit_text(identifier, text, model, case, allowed, mode)
    for extra in extras:
        identifier = f"extra: {extra.get('titulo', 'sem título')}"
        if not (extra.get("secao") and (extra.get("texto") or "").strip() and extra.get("motivo")):
            problems.append(f"{identifier}: tópico extra precisa de seção, texto e motivo.")
            continue
        entry = {
            "id": identifier,
            "secao": extra["secao"],
            "modo": "adaptar_ao_caso",
            "titulo": extra.get("titulo"),
            "imprime_titulo": bool(extra.get("titulo")),
            "status": "pendente_decisao",
            "citacoes": [],
            "texto": extra["texto"],
        }
        _insert_extra(final, entry, extra.get("depois_de"))
        problems += _audit_text(identifier, extra["texto"], model, case, allowed, "adaptar_ao_caso")
    known = {item["id"] for item in drafting_plan["itens"]}
    problems += [f"{identifier}: bloco não previsto no plano." for identifier in sorted(set(drafted) - known)]
    open_items = drafting_plan.get("pendencias") or {}
    marks = sorted(
        {mark for item in final for mark in PENDING.findall(item["texto"] + " " + (item.get("titulo") or ""))}
    )
    if marks and not open_items.get("confirmado_por"):
        problems.append("Há pendências no texto sem confirmação de quem decidiu seguir.")
    return {
        "ok": not problems,
        "problemas": problems,
        "itens": final,
        "pendencias": {**open_items, "marcadas_no_texto": marks},
        "revisao": {
            "citacoes_nao_validadas": sorted({name for item in final for name in item["citacoes"]}),
            "citacoes_novas": new_citations,
            "blocos_com_decisao_pendente": [item["id"] for item in final if item["status"] == "pendente_decisao"],
            "blocos_omitidos": omitted,
            "topicos_extras": [item.get("titulo") for item in extras],
            "ajustes_autorizados": {key: value.get("motivo") for key, value in adjustments.items()},
            "nao_possui": drafting_plan["nao_possui"],
            "alertas": drafting_plan["alertas"],
        },
        "valor_causa": case.get("calc.valor_causa"),
    }


def _insert_extra(final, entry, after):
    positions = [index for index, item in enumerate(final) if item["id"] == after]
    if not positions:
        positions = [index for index, item in enumerate(final) if item["secao"] == entry["secao"]]
    final.insert(positions[-1] + 1 if positions else len(final), entry)


def _fold(text):
    decomposed = unicodedata.normalize("NFD", str(text))
    return "".join(char for char in decomposed if unicodedata.category(char) != "Mn").casefold()


def _mentions(text, value):
    return re.search(rf"(?<!\w){re.escape(_fold(value))}(?!\w)", _fold(text)) is not None


def _audit_text(identifier, text, model, case, allowed, mode):
    problems = []
    if LEFTOVER.search(text):
        problems.append(f"{identifier}: há campo ou marcador sem preencher.")
    if any(_mentions(text, value) for value in case.other_people()):
        problems.append(f"{identifier}: cita dado de outro passageiro.")
    wrong = WRONG_GENDER.get(case.get("autor.genero"))
    if wrong and case.get("autor.numero") != "plural" and wrong.search(text):
        problems.append(f"{identifier}: gênero do autor incorreto.")
    if mode not in DRAFTED:
        return problems
    for match in CITATION.finditer(text):
        digits = _digits(match.group(0))
        if digits and digits not in allowed["citation_digits"]:
            problems.append(f"{identifier}: citação fora do modelo: {match.group(0).strip()}.")
    for number in ARTICLE.findall(text):
        if number.rstrip(".") not in allowed["articles"]:
            problems.append(f"{identifier}: artigo fora do modelo: art. {number.rstrip('.')}.")
    for pattern, label in ((MONEY, "valor"), (DATE, "data")):
        for value in pattern.findall(text):
            if value not in allowed["facts"]:
                problems.append(f"{identifier}: {label} sem base no dossiê: {value}.")
    return problems
