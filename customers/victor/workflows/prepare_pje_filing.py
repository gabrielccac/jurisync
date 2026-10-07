"""Prepare a TRF5 PJe draft payload from a customer's Advbox 00-PROTOCOLAR folder.

    python -m customers.victor.workflows.prepare_pje_filing prepare --customer ID --facts case-facts.json --out RUN_DIR
    python -m customers.victor.workflows.prepare_pje_filing finalize --review RUN_DIR/review.json --facts case-facts.json [--overrides overrides.json]

Downloads the folder, converts DOC/DOCX to PDF, classifies documents, splits
oversized supporting PDFs, and writes review.json (plus payload.json once no
blocker remains). The PJe connector receives that explicit payload; it never
runs customer policy.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import unicodedata
from pathlib import Path

from jurisync.connectors.advbox.client import AdvboxClient
from jurisync.connectors.advbox.documents import AdvboxDocuments
from jurisync.core.pdf import analyze_document
from jurisync.core.word_to_pdf import convert_word_to_pdf

from .pje_defaults import UNCERTAIN_SUPPORTING_TYPE, victor_defaults
from .pje_documents import claim_value_candidates, classify_document, cpf_candidates, document_kind
from .split_pdf import split_pdf

PJE_LIMIT = 10_000_000
SUPPORTING_TYPES = {
    "Planilha",
    "Outros Documentos",
    "Documento de Identificação",
    "Processo Administrativo",
    "Documento Comprobatório",
    "Carta de Indeferimento",
    "Carta de Concessão",
    "Laudo Médico",
}
PROTOCOLAR_FOLDERS = (r"(?:^|/)00\s*-\s*PROTOCOLAR$", r"(?:^|/)00\s*-\s*PROTOCOLAR\s+JUD$")


def _sha256(content):
    return hashlib.sha256(content).hexdigest()


def _digits(value):
    return re.sub(r"\D", "", str(value or ""))


def _write_private(path, value):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        json.dump(value, output, ensure_ascii=False, indent=2)


def require_facts(facts, defaults):
    if facts.get("subject") and facts.get("subjects"):
        raise ValueError("Use subject or subjects, not both")
    subjects = facts.get("subjects") or ([facts["subject"]] if facts.get("subject") else [])
    if not isinstance(subjects, list) or not subjects:
        raise ValueError("subjects must be a nonempty list")
    for index, subject in enumerate(subjects):
        for field in ("search", "code", "name"):
            if not isinstance((subject or {}).get(field), str) or not subject[field].strip():
                raise ValueError(f"subjects[{index}].{field} is required")
    if len({subject["code"] for subject in subjects}) != len(subjects):
        raise ValueError("subjects must have unique codes")
    author = facts.get("author") or {}
    classification = facts.get("classification") or {}
    characteristics = facts.get("characteristics") or {}
    required = {
        "authorCpf": facts.get("authorCpf"),
        "author.name": author.get("name"),
        "classification.jurisdiction": classification.get("jurisdiction"),
        "classification.judicialClass": classification.get("judicialClass"),
        "characteristics.claimValue": characteristics.get("claimValue"),
        "VICTOR_PJE_GRAZIELE_CPF": defaults["attorney"]["cpf"],
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise ValueError(f"Missing case facts or office setting: {', '.join(missing)}")
    if not re.fullmatch(r"\d{11}", _digits(facts["authorCpf"])):
        raise ValueError("authorCpf must have 11 digits")
    if "address" in author:
        address = author["address"]
        if not address or not re.fullmatch(r"\d{5}-?\d{3}", str(address.get("cep") or "")):
            raise ValueError("author.address.cep must have 8 digits")
        if "numero" in address and (not isinstance(address["numero"], str) or not address["numero"].strip()):
            raise ValueError("author.address.numero must be a nonempty string")
    if not re.fullmatch(r"\d+(?:\.\d{3})*,\d{2}", str(characteristics["claimValue"])):
        raise ValueError("claimValue must be Brazilian currency, e.g. 5.000,00")
    for key in ("legalAid", "injunction"):
        if key in characteristics and not isinstance(characteristics[key], bool):
            raise ValueError(f"characteristics.{key} must be a boolean")


def build_payload(facts, documents):
    defaults = victor_defaults()
    require_facts(facts, defaults)
    author = facts["author"]
    return {
        **defaults,
        "classification": {"area": "DIREITO PREVIDENCIÁRIO", **facts["classification"]},
        "subjects": facts.get("subjects") or [facts["subject"]],
        "authorCpf": _digits(facts["authorCpf"]),
        "author": {"name": author["name"], **({"address": author["address"]} if author.get("address") else {})},
        "characteristics": {
            **defaults["characteristics"],
            **facts["characteristics"],
            "priority": defaults["characteristics"]["priority"],
        },
        "documents": documents,
    }


def apply_supporting_fallback(classification):
    """Uncertain supporting files go in as Outros Documentos, flagged, instead of blocking the draft."""
    if classification.get("role") != "supporting" or not classification.get("review"):
        return classification
    details = {key: value for key, value in classification.items() if key not in ("review", "type")}
    result = {
        **details,
        "type": UNCERTAIN_SUPPORTING_TYPE,
        "method": "fallback",
        "fallbackReason": classification["review"],
    }
    if classification.get("type"):
        result["suggestedType"] = classification["type"]
    return result


def supporting_documents(rows):
    documents = []
    for row in rows:
        if row["role"] != "supporting":
            continue
        stem = Path(row["name"]).stem
        parts = row.get("parts")
        if not parts:
            documents.append({"path": row["path"], "type": row["type"], "description": stem})
            continue
        for index, part in enumerate(parts, 1):
            documents.append(
                {
                    "path": part["path"],
                    "type": row["type"],
                    "description": f"{stem} - volume {index}/{len(parts)}",
                    "number": f"{index}/{len(parts)}",
                }
            )
    return documents


def _main_document(rows):
    main = next(row for row in rows if row["role"] == "main")
    return {
        "main": {"path": main["path"], "type": "Petição inicial", "description": "Petição inicial"},
        "supporting": supporting_documents(rows),
    }


def _petition_candidates(path):
    markdown = analyze_document(Path(path).read_bytes(), Path(path).name).get("markdown") or ""
    return claim_value_candidates(markdown), cpf_candidates(markdown)


def _safe_classify(name, text):
    try:
        return classify_document(name, text)
    except Exception as error:  # Classification problems become review items, not crashes.
        return {"role": "supporting", "type": None, "method": "jev", "review": str(error)}


def _is_pdf(content):
    return content[:5] == b"%PDF-"


def upload_filename(name):
    """Clean upload name: no accents (PJe garbles them), no ADVBOX random suffix, one space between words."""
    stem = re.sub(r"_\d{3,}(?=(-vol-\d+)?$)", "", Path(name).stem)
    folded = unicodedata.normalize("NFKD", stem).encode("ascii", "ignore").decode()
    folded = re.sub(r'[\x00-\x1f<>:"/\\|?*#%&]', " ", folded)
    return f"{re.sub(r'[ _]{2,}', ' ', folded).strip() or 'documento'}.pdf"


def _stage_upload(path, directory):
    """Copy a ready-to-upload PDF under its clean name; the content (and hash) is unchanged."""
    source = Path(path)
    target = directory / "upload" / _sha256(source.read_bytes())[:12] / upload_filename(source.name)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    return str(target)


def _prepare_row(entry, index, directory, documents, unlabeled_petition, seen_hashes):
    file = documents.download_file(
        entry.get("downloadUrl") or entry["url"], filename=entry["name"], directory=directory / "files", inspect=False
    )
    content = Path(file["path"]).read_bytes()
    sha256 = _sha256(content)
    ambiguous = document_kind(entry["name"]) in ("decision", "medical", "unknown")
    analysis = (
        analyze_document(content, entry["name"]) if ambiguous and len(content) <= PJE_LIMIT and _is_pdf(content) else {}
    )
    if entry is unlabeled_petition:
        classification = {
            "role": "main",
            "type": "Petição inicial",
            "method": "folder-context",
            "review": "Confirm this unlabeled editable file is the main petition",
        }
    else:
        classification = _safe_classify(entry["name"], analysis.get("markdown") or "")
    if (
        classification.get("review")
        and ambiguous
        and len(content) <= 2_000_000
        and _is_pdf(content)
        and (analysis.get("textCharacters") or 0) < 80
    ):
        analysis = analyze_document(content, entry["name"], ocr=True)
        classification = _safe_classify(entry["name"], analysis.get("markdown") or "")
    row = {
        "id": f"{sha256[:12]}:{index}",
        "name": entry["name"],
        "path": file["path"],
        "sha256": sha256,
        "pages": analysis.get("pageCount"),
        "textCharacters": analysis.get("textCharacters") or 0,
        **apply_supporting_fallback(classification),
    }
    if sha256 in seen_hashes:
        row["review"] = "Duplicate file contents in folder"
    seen_hashes.add(sha256)
    path = Path(row["path"])
    if path.suffix.lower() != ".pdf":
        if path.suffix.lower() in (".doc", ".docx"):
            try:
                row["path"] = str(convert_word_to_pdf(path, directory / "converted" / sha256 / f"{path.stem}.pdf"))
            except Exception as error:
                row["review"] = f"PDF conversion failed: {error}"
        else:
            row["review"] = "Document is not a PDF or convertible Word file"
    if Path(row["path"]).suffix.lower() == ".pdf":
        upload = Path(row["path"]).read_bytes()
        row["uploadSha256"] = _sha256(upload)
        if not _is_pdf(upload):
            row["review"] = "Converted document is not a PDF"
        elif len(upload) > PJE_LIMIT:
            if row["role"] != "supporting":
                row["review"] = "Main petition exceeds PJe's 10 MB limit"
            else:
                try:
                    row["parts"] = split_pdf(row["path"], directory / "split" / sha256)
                    for part in row["parts"]:
                        part["uploadSha256"] = _sha256(Path(part["path"]).read_bytes())
                    row["review"] = "; ".join(
                        filter(None, [row.get("review"), "Confirm split PDF page ranges before upload"])
                    )
                except Exception as error:
                    row["review"] = f"PDF splitting needs review: {error}"
        row["path"] = _stage_upload(row["path"], directory)
        for part in row.get("parts") or []:
            part["path"] = _stage_upload(part["path"], directory)
    return row


def prepare_customer(customer_id, facts, out_dir, *, folder=None, client=None):
    directory = Path(out_dir).resolve()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    documents = AdvboxDocuments(client or AdvboxClient())
    root = documents.list_customer_files(customer_id)
    selected = folder or next(
        (
            entry["path"]
            for pattern in PROTOCOLAR_FOLDERS
            for entry in root["folders"]
            if re.search(pattern, entry["path"].strip(), re.I)
        ),
        None,
    )
    if not selected:
        raise ValueError(f"No 00-PROTOCOLAR folder found for customer {customer_id}")
    files = documents.list_customer_files(customer_id, selected)["files"]
    if not files:
        raise ValueError(f"The selected folder has no files: {selected}")
    editable = [entry for entry in files if re.search(r"\.docx?$", entry["name"], re.I)]
    unlabeled = (
        editable[0]
        if len(editable) == 1 and not any(document_kind(entry["name"]) == "petition" for entry in files)
        else None
    )
    seen = set()
    rows = [_prepare_row(entry, index, directory, documents, unlabeled, seen) for index, entry in enumerate(files)]

    mains = [row for row in rows if row["role"] == "main"]
    review = [{"id": row["id"], "name": row["name"], "reason": row["review"]} for row in rows if row.get("review")]
    if len(mains) != 1:
        review.append({"name": "petition", "reason": f"Expected one main petition, found {len(mains)}"})
    candidates = {"claimValues": [], "cpfs": []}
    if len(mains) == 1 and mains[0].get("uploadSha256"):
        candidates["claimValues"], candidates["cpfs"] = _petition_candidates(mains[0]["path"])
        claim_value = (facts.get("characteristics") or {}).get("claimValue")
        if claim_value and candidates["claimValues"] and claim_value not in candidates["claimValues"]:
            review.append({"name": "claimValue", "reason": "Case fact differs from the petition's stated claim value"})
        if facts.get("authorCpf") and candidates["cpfs"] and _digits(facts["authorCpf"]) not in candidates["cpfs"]:
            review.append(
                {"name": "authorCpf", "reason": "Author CPF was not found among the petition's formatted CPFs"}
            )
    try:
        require_facts(facts, victor_defaults())
    except ValueError as error:
        review.append({"name": "case facts", "reason": str(error)})
    payload_path = None
    if not review:
        try:
            payload = build_payload(facts, _main_document(rows))
            payload_path = directory / "payload.json"
            _write_private(payload_path, payload)
        except ValueError as error:
            review.append({"name": "case facts", "reason": str(error)})
    fallbacks = [
        {
            "id": row["id"],
            "name": row["name"],
            "type": row["type"],
            "reason": row["fallbackReason"],
            **({"suggestedType": row["suggestedType"]} if row.get("suggestedType") else {}),
        }
        for row in rows
        if row.get("fallbackReason")
    ]
    report = {
        "customerId": str(customer_id),
        "folder": selected,
        "files": rows,
        "candidates": candidates,
        "review": review,
        "ready": not review,
        "fallbacks": fallbacks,
    }
    _write_private(directory / "review.json", report)
    return {
        "ready": report["ready"],
        "review": review,
        "folder": selected,
        "fileCount": len(rows),
        "fallbackCount": len(fallbacks),
        "reviewPath": str(directory / "review.json"),
        "payloadPath": str(payload_path) if payload_path else None,
    }


def _verify_row(row):
    if row["role"] not in ("main", "supporting"):
        raise ValueError(f"Invalid role for {row['name']}")
    if row["role"] == "main" and row.get("type") != "Petição inicial":
        raise ValueError(f"Invalid main type for {row['name']}")
    parts = row.get("parts")
    if parts is not None and (not isinstance(parts, list) or not parts):
        raise ValueError(f"Invalid PDF volumes: {row['name']}")
    if row["role"] == "main" and parts:
        raise ValueError(f"Main petition must be one PDF: {row['name']}")
    if row["role"] == "supporting" and row.get("type") not in SUPPORTING_TYPES:
        raise ValueError(f"Invalid PJe type for {row['name']}")
    if row.get("review") or not row.get("type") or not row.get("uploadSha256"):
        raise ValueError(f"Document needs review: {row['name']}")
    content = Path(row["path"]).read_bytes()
    if _sha256(content) != row["uploadSha256"]:
        raise ValueError(f"Document changed: {row['name']}")
    if (not parts and len(content) > PJE_LIMIT) or not _is_pdf(content):
        raise ValueError(f"Invalid PDF: {row['name']}")
    for part in parts or []:
        part_content = Path(part["path"]).read_bytes()
        if len(part_content) > PJE_LIMIT or not _is_pdf(part_content) or _sha256(part_content) != part["uploadSha256"]:
            raise ValueError(f"Split PDF changed or is invalid: {part['path']}")


def finalize_review(review_path, facts, overrides=None):
    """Apply the reviewer's explicit choices and write payload.json next to review.json."""
    overrides = overrides or {}
    report = json.loads(Path(review_path).read_text(encoding="utf-8"))
    rows = []
    for original in report["files"]:
        choice = overrides.get(original["id"])
        row = {**original, **(choice or {})}
        if choice:
            row.pop("review", None)
        if row["role"] == "skip":
            continue
        _verify_row(row)
        rows.append(row)
    mains = [row for row in rows if row["role"] == "main"]
    if len(mains) != 1:
        raise ValueError(f"Expected one main petition, found {len(mains)}")
    claim_values, cpfs = _petition_candidates(mains[0]["path"])
    if claim_values and (facts.get("characteristics") or {}).get("claimValue") not in claim_values:
        raise ValueError("Case claimValue differs from the selected petition's stated value")
    if cpfs and _digits(facts.get("authorCpf")) not in cpfs:
        raise ValueError("Author CPF was not found among the selected petition's formatted CPFs")
    payload = build_payload(facts, _main_document(rows))
    destination = Path(review_path).resolve().parent / "payload.json"
    _write_private(destination, payload)
    return {"ready": True, "payloadPath": str(destination), "documentCount": len(rows)}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Prepare Victor's TRF5 PJe filing payload from Advbox")
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--customer", required=True)
    prepare.add_argument("--facts", required=True, type=Path)
    prepare.add_argument("--out", required=True, type=Path)
    prepare.add_argument("--folder")
    finalize = commands.add_parser("finalize")
    finalize.add_argument("--review", required=True, type=Path)
    finalize.add_argument("--facts", required=True, type=Path)
    finalize.add_argument("--overrides", type=Path)
    args = parser.parse_args(argv)
    facts = json.loads(args.facts.read_text(encoding="utf-8"))
    if args.command == "prepare":
        result = prepare_customer(args.customer, facts, args.out, folder=args.folder)
    else:
        overrides = json.loads(args.overrides.read_text(encoding="utf-8")) if args.overrides else {}
        result = finalize_review(args.review, facts, overrides)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    from jurisync.core.mcp import load_env_file

    load_env_file(Path(__file__).resolve().parents[3] / ".env")
    main()
