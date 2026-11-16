"""Audit a finished draft against the case file and the office's references before it is written to Word.

The drafter writes freely; this checks what a language model gets wrong easily and a reviewer may miss: amounts and
dates without a source, case law not found in the office's references, another passenger's data, leftover markers,
missing images and a claim value different from the calculated one.
"""

import json
import re
import unicodedata
from pathlib import Path

from jurisync.workflows.petitions import calc, minuta, render
from jurisync.workflows.petitions.calc import digits
from jurisync.workflows.petitions.case import AuthorCase

NUMBER = r"(?:n[ºo°.]*\s*)?\d[\d.\s/-]*\d"
CITATION = re.compile(
    rf"\b(?:REsp|AREsp|AgInt|AgRg|ARE|RE|HC|Tema|S[úu]mula|Apela[çc][ãa]o(?:\s+C[íi]vel)?|AC|Recurso\s+Inominado(?:\s+C[íi]vel)?"
    rf"|RI|Processo)\s*(?:n[ºo°.]*\s*)?:?\s*{NUMBER}"
    r"|\b\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}\b",
    re.I,
)
MONEY = re.compile(r"R\$\s*:?\s*(\d[\d.]*,\d+)")
DATE = re.compile(r"\b\d{2}/\d{2}/\d{4}\b")
LEFTOVER = re.compile(r"\{\{|\}\}|\[PREENCHER|\[INSERIR|\[trecho|XXX|\?\?\?", re.I)
# Only the claim value sentence ("Atribui-se à causa o valor de R$ ...", "Dá-se à causa..."), never "no valor de R$" in an ementa.
CLAIM_VALUE = re.compile(
    r"(?:valor\s+da\s+causa|à\s+causa\s+o\s+valor|causa\s+o\s+valor)[^R]{0,40}R\$\s*(\d[\d.]*,\d{2})", re.I
)
REFERENCE_SUFFIXES = (".md", ".txt", ".json")
# Phrases the office's lawyer forbade (review of 13/11/2026): they hand the defence a doubt about the cause of the
# cancellation, or treat a claim the office asks in full as a fallback.
FORBIDDEN = (
    (re.compile(r"n[ãa]o\s+h[áa],?\s+nos\s+documentos", re.I), '"não há, nos documentos" dá brecha à defesa'),
    (re.compile(r"sem\s+justificativa\s+publicada", re.I), '"sem justificativa publicada" dá brecha à defesa'),
    (
        re.compile(r"subsidiari\w*[^.;]{0,120}(?:\bDES\b|Direitos?\s+Especia\w*\s+de\s+Saque|preteri[çc][ãa]o)", re.I),
        "DES e preterição são pedidos principais/sucessivos, nunca subsidiários",
    ),
    (re.compile(r"ex[íi]gu[oa]", re.I), 'palavra difícil ("exíguo"): use a comum'),
)
# Assistance the airline gave must not be acknowledged; the law quoted may name it, so this is only a warning.
ASSISTANCE = re.compile(
    r"(?:R[ée]u?s?|companhia|cia\.?)\s+(?:\w+\s+){0,4}(?:forneceu|ofereceu|providenciou|disponibilizou|custeou|limitou-se)",
    re.I,
)


def references_text(paths):
    """The office's references as one text: guides, mapped petitions, citation catalog, extra .txt files."""
    parts = []
    for path in paths or ():
        path = Path(path)
        files = (
            sorted(item for item in path.rglob("*") if item.suffix in REFERENCE_SUFFIXES) if path.is_dir() else [path]
        )
        for file in files:
            if not file.is_file():
                continue
            text = file.read_text(encoding="utf-8", errors="replace")
            if file.suffix == ".json":
                try:
                    text = json.dumps(json.loads(text), ensure_ascii=False)
                except ValueError:
                    pass
            parts.append(text)
    return "\n".join(parts)


def _whole(value, text):
    """The value as a whole number or date in the text, not as a piece of a longer one (7,54 inside 7,5410)."""
    return re.search(rf"(?<![\d.,]){re.escape(value)}(?![\d]|[.,]\d)", text) is not None


def _fold(text):
    decomposed = unicodedata.normalize("NFD", str(text))
    return "".join(char for char in decomposed if unicodedata.category(char) != "Mn").casefold()


def _mentions(text, value):
    return re.search(rf"(?<!\w){re.escape(_fold(value))}(?!\w)", _fold(text)) is not None


def _iso_to_br(text):
    return text + "\n" + "\n".join(f"{d}/{m}/{y}" for y, m, d in re.findall(r"(\d{4})-(\d{2})-(\d{2})", text))


def audit(draft_text, dossier, group, references="", image_root=None, case_references=""):
    """Problems block the draft; warnings go to the reviewer with it."""
    case = AuthorCase(dossier, group[0], group=group)
    calc.apply(case)
    items = minuta.parse(draft_text)
    problems, warnings = [], []
    facts = _iso_to_br(json.dumps(dossier, ensure_ascii=False) + json.dumps(case.calculated, ensure_ascii=False))
    reference_digits = {digits(run) for run in re.findall(r"\d[\d.\s/-]*\d|\d", references)}
    # Texts of office petitions the drafter read for this case: they let a quoted ementa through, for review.
    case_digits = {digits(run) for run in re.findall(r"\d[\d.\s/-]*\d|\d", case_references)}
    to_check_at_source = []
    new_citations = dossier.get("citacoes_novas") or []
    allowed_new = {digits(item.get("referencia")) for item in new_citations if item.get("fonte")}
    problems += [f"Citação nova sem fonte: {item.get('referencia')}" for item in new_citations if not item.get("fonte")]
    cited = []
    for position, item in enumerate(items, 1):
        where = f"§{position} ({item['tipo']})"
        text = minuta.plain(item)
        if LEFTOVER.search(text):
            problems.append(f"{where}: marcador ou campo sem preencher.")
        # The signature names the office's lawyers: a client's relative with the same first name is not cited there.
        for value in [] if item["tipo"] in ("assinatura", "fecho") else case.other_people():
            if _mentions(text, value):
                problems.append(f"{where}: cita dado de pessoa que não é autora desta ação ({value[:3]}…).")
        if item["tipo"] == "imagem":
            try:
                render.image_file(item["arquivo"], image_root)
            except ValueError as error:
                problems.append(f"{where}: {error}")
            continue
        for match in CITATION.finditer(text):
            number = digits(match.group(0))
            reference = " ".join(match.group(0).split())
            if reference not in cited:
                cited.append(reference)
            if number and number not in reference_digits and number not in allowed_new:
                if number in case_digits:
                    if reference not in to_check_at_source:
                        to_check_at_source.append(reference)
                else:
                    problems.append(f"{where}: julgado ou súmula fora das referências do escritório: {reference}.")
        if item["tipo"] == "citacao":
            continue  # an ementa quotes the amounts and dates of another case
        for pattern, label in FORBIDDEN:
            if pattern.search(text):
                problems.append(f"{where}: {label}.")
        if ASSISTANCE.search(text):
            warnings.append(f"{where}: parece reconhecer assistência prestada pela ré; diga só que não foi adequada.")
        for pattern, label in ((MONEY, "valor"), (DATE, "data")):
            for value in pattern.findall(text):
                if not _whole(value, facts) and not _whole(value, references):
                    problems.append(f"{where}: {label} sem base no dossiê nem nos cálculos: {value}.")
    full = "\n".join(minuta.plain(item) for item in items if item["tipo"] not in ("citacao", "tabela", "imagem"))
    expected = case.calculated.get("valor_causa")
    for value in CLAIM_VALUE.findall(full):
        if expected and value != expected:
            problems.append(f"Valor da causa R$ {value} diferente do calculado (R$ {expected}).")
    if expected and not CLAIM_VALUE.search(full):
        warnings.append(f"Não achei o valor da causa no texto; o calculado é R$ {expected}.")
    for text in [
        str(item.get("item") if isinstance(item, dict) else item) for item in dossier.get("pendencias_abertas") or []
    ] + [str(item) for item in dossier.get("documentos_a_juntar") or []]:
        if any(_mentions(text, value) for value in case.other_people()):
            problems.append("Página de pendências: cita pessoa que não é autora desta ação.")
            break
    if to_check_at_source:
        warnings.append(
            "Citações achadas só nos textos de petição lidos para este caso: conferir na fonte antes do protocolo."
        )
    marks = minuta.pending_marks(items)
    if marks and not dossier.get("seguir_com_pendencias"):
        problems.append("Há [PENDENTE] no texto sem 'seguir_com_pendencias' (quem decidiu seguir) no dossiê.")
    if case.calculated.get("acima_do_teto"):
        warnings.append("A soma dos pedidos passa do teto do juizado: a advogada decide o que reduzir.")
    if case.calculated.get("des_sem_cotacao"):
        warnings.append("O DES está pedido, mas sem cotação: o valor da causa calculado não inclui o DES.")
    if case.calculated.get("despesas_pagas_por_terceiro"):
        warnings.append("Há despesa paga por quem não é autor (pago_por): a advogada confirma se entra no pedido.")
    if case.calculated.get("despesas_sem_pagador"):
        warnings.append("Há despesa sem pago_a: só o que a ré cobrou vai em dobro.")
    return {
        "ok": not problems,
        "problemas": problems,
        "avisos": warnings,
        "pendencias_no_texto": marks,
        "citacoes_para_conferir": cited,
        "citacoes_novas": new_citations,
        "citacoes_conferir_na_fonte": to_check_at_source,
        "calculos": case.calculated,
        "itens": items,
    }
