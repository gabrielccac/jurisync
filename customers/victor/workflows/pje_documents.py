"""Classify Victor's Advbox filing documents into PJe document types.

Clear filenames decide directly; decision letters are read for their outcome;
ambiguous readable files are sent to TypeSafe Jev (filename + at most 12,000
extracted characters, never the PDF itself).
"""

import json
import os
import re
import time
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path

JEV_TEXT_LIMIT = 12_000
CRITERIA = {
    "denial": "Comunicado de indeferimento ou negativa de benefício/requerimento",
    "grant": "Comunicado de deferimento ou concessão de benefício",
    "cessation": "Cessação, suspensão ou recusa de prorrogar benefício já existente",
    "medical_report": "Laudo médico individual com avaliação clínica ou pericial",
    "medical_bundle": "Conjunto de receitas, exames, prontuários ou provas médicas",
    "administrative_file": "Cópia do processo administrativo completo",
    "supporting_evidence": "Prova documental comum, inclusive CNIS, CTPS, residência ou atividade rural",
    "other": "Outro conteúdo ou conteúdo insuficiente para decidir",
}
JEV_TYPES = {
    "denial": "Carta de Indeferimento",
    "grant": "Carta de Concessão",
    "medical_report": "Laudo Médico",
    "medical_bundle": "Documento Comprobatório",
    "administrative_file": "Processo Administrativo",
    "supporting_evidence": "Documento Comprobatório",
}


def plain(value):
    text = unicodedata.normalize("NFD", str(value if value is not None else ""))
    return "".join(char for char in text if not unicodedata.combining(char)).upper()


def document_kind(name):
    stem = re.sub(r"[\s_]+\d{3,}$", "", re.sub(r"^\d+[\s._-]+", "", plain(Path(name).stem)))
    rules = (
        (r"PETI?CAO|MANDADO DE SEGURANCA", "petition"),
        (r"^INDEFERIMENTO$", "Carta de Indeferimento"),
        (r"^CONCESSAO$", "Carta de Concessão"),
        (r"COMUNICAD[OA].*DECISAO|COMUNICACAO.*DECISAO|COMUNICAD[OA].*CESSACAO", "decision"),
        (r"VALOR DA CAUSA|PLANILHA|\bRMI\b", "Planilha"),
        (r"QUESITOS", "Outros Documentos"),
        (r"PROCURACAO|CONTRATO", "Outros Documentos"),
        (r"IDENTIFI.*REPRESENTAN", "Documento Comprobatório"),
        (r"DOCUMENTO DE IDENTIFICACAO|\bRG\b|\bCNH\b", "Documento de Identificação"),
        (r"^PA\b|PROCESSO ADMINISTRATIVO", "Processo Administrativo"),
        (r"LAUDO|MEDIC|RECEITUARIO|EXAME", "medical"),
        (
            r"CNIS|CTPS|RESIDENCIA|ATIVIDADE RURAL|PROVAS RURAIS|GRUPO FAMILIAR|CADUNICO|LOAS|COMPROVANTE"
            r"|CERTIDAO DE OBITO|DEPENDENCIA ECONOMICA",
            "Documento Comprobatório",
        ),
    )
    return next((kind for pattern, kind in rules if re.search(pattern, stem)), "unknown")


def decision_outcome(text):
    normalized = re.sub(r"\s+", " ", re.sub(r"[*_#]", " ", plain(text)))
    if len(normalized) < 80:
        return "unknown"
    if re.search(
        r"CESSACAO|CESSADO|NAO PRORROG|NAO FOI PRORROG|PRORROGACAO.*INDEFERID|REABILITACAO.*CONCLUID", normalized
    ):
        return "cessation"
    denied = re.search(
        r"\bINDEFERIMENTO DO (?:SEU )?PEDIDO\b|DECISAO\s*:\s*NEGADO|\bPEDIDO\s+NEGADO\b"
        r"|\bREQUERIMENTO\s+INDEFERIDO\b|DIREITO.*NAO (?:FOI )?RECONHECID",
        normalized,
    )
    granted = re.search(r"\bDEFERIMENTO DO PEDIDO\b|DECISAO\s*:\s*DEFERIDO|\bBENEFICIO\s+CONCEDIDO\b", normalized)
    if denied and granted:
        return "mixed"
    return "denied" if denied else "granted" if granted else "unknown"


def claim_value_candidates(text):
    pattern = (
        r"VALOR\s+DA\s+CAUSA[^\n]{0,100}?R\$\s*([\d.]+,\d{2})"
        r"|D[AÁ]-?SE\s+[ÀA]\s+CAUSA\s+O\s+VALOR\s+DE\s+R\$\s*([\d.]+,\d{2})"
    )
    return list(dict.fromkeys(match[1] or match[2] for match in re.finditer(pattern, str(text), re.I)))


def cpf_candidates(text):
    return list(
        dict.fromkeys(re.sub(r"\D", "", match) for match in re.findall(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b", str(text)))
    )


def _post_json(url, headers, body, timeout=30):
    request = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        error.close()
        return error.code, None


def ask_jev(name, text, *, post=_post_json, openrouter_key=None, api_key=None, sleep=time.sleep):
    """Ask TypeSafe Jev for a document kind; None when no key is configured."""
    openrouter_key = os.environ.get("OPENROUTER_API_KEY") if openrouter_key is None else openrouter_key
    api_key = os.environ.get("TYPESAFE_API_KEY") if api_key is None else api_key
    key = openrouter_key or api_key
    if not key:
        return None
    url = "https://openrouter.ai/api/alpha/decisions" if openrouter_key else "https://api.typesafe.ai/v1/systemone"
    body = {
        "model": "typesafe/jev-1.13" if openrouter_key else "jev-latest",
        "state": {"filename": name, "extracted_text": str(text)[:JEV_TEXT_LIMIT]},
        "questions": {
            "kind": {
                "type": "choice",
                "instructions": "Classifique o documento previdenciário pelo conteúdo. Distinga indeferimento, concessão "
                "e cessação. Responda other se não houver evidência suficiente.",
                "criteria": CRITERIA,
            }
        },
    }
    for attempt in range(3):
        status, payload = post(url, {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}, body)
        if status in (429, 529) and attempt < 2:
            sleep(0.7 * 2**attempt)
            continue
        if not 200 <= status < 300:
            raise RuntimeError(f"Jev API returned HTTP {status}")
        answer = ((payload or {}).get("answers") or {}).get("kind") or {}
        confidence = answer.get("confidence")
        if (
            answer.get("choice") not in CRITERIA
            or not isinstance(confidence, (int, float))
            or isinstance(confidence, bool)
        ):
            raise RuntimeError("Jev API returned an invalid classification answer")
        return {"choice": answer["choice"], "confidence": confidence}
    raise RuntimeError("Jev API kept returning rate-limit responses")


def classify_document(name, text, **jev_options):
    kind = document_kind(name)
    if kind == "petition":
        return {"role": "main", "type": "Petição inicial", "method": "filename"}
    if kind not in ("decision", "medical", "unknown"):
        return {"role": "supporting", "type": kind, "method": "filename"}
    if kind == "decision":
        if re.search(r"CESSACAO|PRORROGACAO", plain(name)):
            return {
                "role": "supporting",
                "type": None,
                "method": "filename",
                "review": "Cessation or non-extension letter",
            }
        outcome = decision_outcome(text)
        if outcome == "denied":
            return {"role": "supporting", "type": "Carta de Indeferimento", "method": "content"}
        if outcome == "granted":
            return {"role": "supporting", "type": "Carta de Concessão", "method": "content"}
        if outcome in ("cessation", "mixed"):
            return {"role": "supporting", "type": None, "method": "content", "review": f"Decision outcome: {outcome}"}
    if len(str(text).strip()) < 80:
        return {"role": "supporting", "type": None, "method": "content", "review": "No readable document text"}
    jev = ask_jev(name, text, **jev_options)
    if not jev:
        return {
            "role": "supporting",
            "type": None,
            "method": "content",
            "review": "Set OPENROUTER_API_KEY or TYPESAFE_API_KEY for Jev classification",
        }
    proposed = JEV_TYPES.get(jev["choice"])
    bundled_medical = kind == "medical" and re.search(
        r"LAUDOS|DOCUMENTOS MED|EXAMES|RECEITUARIOS|ADMINISTRATIVO", plain(name)
    )
    if kind == "decision":
        compatible = jev["choice"] in ("denial", "grant")
    elif kind == "medical":
        compatible = not bundled_medical and jev["choice"] in ("medical_report", "medical_bundle")
    else:
        compatible = False  # Unknown files also need a human inclusion decision.
    result = {"role": "supporting", "type": proposed, "method": "jev", "confidence": jev["confidence"]}
    if not proposed or not compatible or jev["confidence"] < 0.9:
        result["review"] = "Jev classification needs review"
    return result
