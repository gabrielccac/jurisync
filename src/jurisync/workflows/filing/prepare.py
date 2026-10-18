"""Prepare a TRF5 PJe draft payload from an input folder (see contract.py), driven by a filing profile.

    python -m jurisync.workflows.filing prepare --profile NAME --from FOLDER --facts case-facts.json --out RUN_DIR
    python -m jurisync.workflows.filing finalize --profile NAME --run RUN_DIR --facts case-facts.json [--overrides overrides.json]
    python -m jurisync.workflows.filing approve --run RUN_DIR

Copies the folder into the run's inbox, converts DOC/DOCX and images to PDF, classifies documents, splits
oversized supporting PDFs, and writes review.json (plus payload.json once no
blocker remains). The PJe connector receives that explicit payload; it never
runs customer policy, and it writes nothing until `approve` (after the user has
seen the payload) records approval.json for that exact payload.
"""

import argparse
import hashlib
import json
import re
import unicodedata
from pathlib import Path

from jurisync.connectors.pje.filing.approval import approve
from jurisync.connectors.pje.filing.approval import revoke as revoke_approval
from jurisync.connectors.pje.filing.checkpoint import now
from jurisync.connectors.pje.filing.store import CHECKPOINT, DOCUMENTS, MANIFEST, PAYLOAD, REVIEW, WORK, LocalRunStore
from jurisync.core.pdf import analyze_document
from jurisync.core.word_to_pdf import convert_word_to_pdf

from .classify import claim_value_candidates, classify_document, cpf_candidates, document_kind
from .images import IMAGE_SUFFIXES, image_to_pdf
from .inbox import fill_from_folder, read_inbox
from .profile import load_profile
from .representation import REPRESENTATIVE_KINDS, parse_birth_date, representation_problems, representation_signals
from .split import split_pdf

PJE_LIMIT = 10_000_000
# What a reviewer may change about a file in filing_finalize. Paths and hashes come only from prepare.
OVERRIDE_KEYS = frozenset({"role", "type", "description"})


def _sha256(content):
    return hashlib.sha256(content).hexdigest()


def _digits(value):
    return re.sub(r"\D", "", str(value or ""))


def _case_subjects(facts, defaults):
    """The case's subjects, or the profile's default subjects when the facts name none."""
    if facts.get("subject") and facts.get("subjects"):
        raise ValueError("Use subject or subjects, not both")
    return facts.get("subjects") or ([facts["subject"]] if facts.get("subject") else defaults.get("subjects") or [])


def _case_classification(facts, defaults):
    """Area, jurisdiction and class: the case's values over the profile's defaults (area defaults to previdenciário)."""
    return {
        "area": "DIREITO PREVIDENCIÁRIO",
        **(defaults.get("classification") or {}),
        **(facts.get("classification") or {}),
    }


def _priorities(value, label):
    """A priority setting as a list of PJe labels: one label, a list of labels, or false/absent for none."""
    if value in (None, False, ""):
        return []
    values = [value] if isinstance(value, str) else value
    if not isinstance(values, list) or not all(isinstance(item, str) and item.strip() for item in values):
        raise ValueError(f"{label} must be a PJe priority label, a list of labels, or false")
    return [item.strip() for item in values]


def _case_characteristics(facts, defaults):
    """The profile's characteristics, the case's on top. Priorities: the profile's fixed `priority` always, plus the
    case's `priority` or, when the case names none, the profile's `defaultPriority`."""
    profile = dict(defaults.get("characteristics") or {})
    case = dict(facts.get("characteristics") or {})
    fixed = _priorities(profile.pop("priority", None), "profile characteristics.priority")
    fallback = _priorities(profile.pop("defaultPriority", None), "profile characteristics.defaultPriority")
    chosen = _priorities(case.pop("priority", None), "characteristics.priority") if "priority" in case else fallback
    priorities = list(dict.fromkeys([*fixed, *chosen]))
    return {**profile, **case, "priority": priorities}


def _party_roles(facts, defaults):
    """The 'Tipo da Parte' labels for each pole: the profile's, the case's on top (2nd degree: they follow the class)."""
    return {**(defaults.get("partyRoles") or {}), **(facts.get("partyRoles") or {})}


def require_facts(facts, defaults):
    subjects = _case_subjects(facts, defaults)
    if not isinstance(subjects, list) or not subjects:
        raise ValueError("subjects must be a nonempty list")
    for index, subject in enumerate(subjects):
        for field in ("search", "code", "name"):
            if not isinstance((subject or {}).get(field), str) or not subject[field].strip():
                raise ValueError(f"subjects[{index}].{field} is required")
    if len({subject["code"] for subject in subjects}) != len(subjects):
        raise ValueError("subjects must have unique codes")
    author = facts.get("author") or {}
    classification = _case_classification(facts, defaults)
    characteristics = facts.get("characteristics") or {}
    required = {
        "authorCpf": facts.get("authorCpf"),
        "author.name": author.get("name"),
        "classification.jurisdiction": classification.get("jurisdiction"),
        "classification.judicialClass": classification.get("judicialClass"),
        "characteristics.claimValue": characteristics.get("claimValue"),
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise ValueError(f"Missing case facts: {', '.join(missing)}")
    if "attorney" in facts:
        raise ValueError("Use attorneys (a list of extra attorneys), not attorney")
    if classification.get("referenceProcess") is not None and len(_digits(classification["referenceProcess"])) != 20:
        raise ValueError("classification.referenceProcess must be the CNJ number (20 digits) of the case of origin")
    roles = _party_roles(facts, defaults)
    if set(roles) - {"active", "passive"} or not all(isinstance(v, str) and v.strip() for v in roles.values()):
        raise ValueError("partyRoles maps active/passive to PJe 'Tipo da Parte' labels, e.g. AGRAVANTE / AGRAVADO")
    _check_attorneys(facts.get("attorneys", []))
    if "representative" in author:
        _check_representative(author["representative"])
    if "birthDate" in author:
        parse_birth_date(author["birthDate"])
    if "selfRepresented" in author and not isinstance(author["selfRepresented"], bool):
        raise ValueError("author.selfRepresented must be true or false")
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
    for key in ("legalAid", "injunction", "digitalCourt", "secrecy"):
        if key in characteristics and not isinstance(characteristics[key], bool):
            raise ValueError(f"characteristics.{key} must be a boolean")
    if characteristics.get("secrecy") is True:
        raise ValueError("characteristics.secrecy true is not supported: segredo de justiça is set by hand in PJe")
    _case_characteristics(facts, defaults)


def _check_cpf(value, label):
    if not isinstance(value, str) or not re.fullmatch(r"\d{11}", _digits(value)):
        raise ValueError(f"{label} must have 11 digits")


def _check_attorneys(attorneys):
    """Extra attorneys to associate with the author, besides the one logged in to PJe (who is there by default)."""
    if not isinstance(attorneys, list):
        raise ValueError("attorneys must be a list (empty or omitted when only the logged-in attorney files)")
    seen = set()
    for index, attorney in enumerate(attorneys):
        label = f"attorneys[{index}]"
        if not isinstance(attorney, dict) or set(attorney) != {"cpf", "name", "oabNumber", "oabState"}:
            raise ValueError(f"{label} needs exactly cpf, name, oabNumber and oabState")
        _check_cpf(attorney["cpf"], f"{label}.cpf")
        if not isinstance(attorney["name"], str) or not attorney["name"].strip():
            raise ValueError(f"{label}.name is required")
        if not re.fullmatch(r"\d{1,7}", str(attorney["oabNumber"])):
            raise ValueError(f"{label}.oabNumber must be digits")
        if not re.fullmatch(r"[A-Z]{2}", str(attorney["oabState"])):
            raise ValueError(f"{label}.oabState must be a two-letter UF, e.g. PE")
        if _digits(attorney["cpf"]) in seen:
            raise ValueError(f"{label} repeats an attorney")
        seen.add(_digits(attorney["cpf"]))


def _check_representative(representative):
    """The author's legal representative: who signs for a minor or a represented author."""
    if not isinstance(representative, dict) or not {"cpf", "name", "kind"} <= set(representative) <= {
        "cpf",
        "name",
        "kind",
        "address",
    }:
        raise ValueError("author.representative needs cpf, name and kind (and optionally address)")
    if "address" in representative:
        address = representative["address"]
        if not isinstance(address, dict) or not re.fullmatch(r"\d{5}-?\d{3}", str(address.get("cep") or "")):
            raise ValueError("author.representative.address.cep must have 8 digits")
        if not isinstance(address.get("numero"), str) or not address["numero"].strip():
            raise ValueError("author.representative.address.numero must be a nonempty string")
    _check_cpf(representative["cpf"], "author.representative.cpf")
    if not isinstance(representative["name"], str) or not representative["name"].strip():
        raise ValueError("author.representative.name is required")
    if representative["kind"] not in REPRESENTATIVE_KINDS:
        raise ValueError(f"author.representative.kind must be one of {', '.join(REPRESENTATIVE_KINDS)}")


def _author(author):
    """The author as the payload carries it: name, optional address, optional legal representative."""
    result = {"name": author["name"]}
    if author.get("address"):
        result["address"] = author["address"]
    if author.get("representative"):
        representative = author["representative"]
        result["representative"] = {**representative, "cpf": _digits(representative["cpf"])}
    return result


def select_other_participants(participants, facts):
    """Keep fixed bodies; keep a conditional body only when its case fact is true (e.g. fiscalDaLei)."""
    selected = []
    for participant in participants:
        body = {key: value for key, value in participant.items() if key != "when"}
        condition = participant.get("when")
        if not condition:
            selected.append(body)
            continue
        if not isinstance(facts.get(condition), bool):
            raise ValueError(f"{condition} (true or false) is required to decide {participant['name']}")
        if facts[condition]:
            selected.append(body)
    return selected


def build_payload(facts, documents, defaults):
    require_facts(facts, defaults)
    author = facts["author"]
    payload = {
        **defaults,
        "classification": _case_classification(facts, defaults),
        "subjects": _case_subjects(facts, defaults),
        "authorCpf": _digits(facts["authorCpf"]),
        "author": _author(author),
        "characteristics": _case_characteristics(facts, defaults),
        "documents": documents,
    }
    if "otherParticipants" in defaults:
        payload["otherParticipants"] = select_other_participants(defaults["otherParticipants"], facts)
    if facts.get("attorneys"):
        payload["attorneys"] = [{**item, "cpf": _digits(item["cpf"])} for item in facts["attorneys"]]
    payload.pop("partyRoles", None)
    if _party_roles(facts, defaults):
        payload["partyRoles"] = _party_roles(facts, defaults)
    return payload


def apply_supporting_fallback(classification, uncertain_type="Outros Documentos"):
    """Uncertain supporting files go in as Outros Documentos, flagged, instead of blocking the draft."""
    if classification.get("role") != "supporting" or not classification.get("review"):
        return classification
    details = {key: value for key, value in classification.items() if key not in ("review", "type")}
    result = {
        **details,
        "type": uncertain_type,
        "method": "fallback",
        "fallbackReason": classification["review"],
    }
    if classification.get("type"):
        result["suggestedType"] = classification["type"]
    return result


def _portal_types(classification, profile):
    """Classification speaks TRF5's type names; the profile turns them into its portal's labels."""
    result = dict(classification)
    if result.get("role") == "supporting":
        for key in ("type", "suggestedType"):
            if result.get(key):
                result[key] = profile.portal_type(result[key])
    return result


def _petition_candidates(path):
    """What the petition states: claim values, formatted CPFs and phrases saying the author is represented."""
    markdown = analyze_document(Path(path).read_bytes(), Path(path).name).get("markdown") or ""
    return claim_value_candidates(markdown), cpf_candidates(markdown), representation_signals(markdown)


def _safe_classify(name, text, jev_options=None, rules=None):
    try:
        return classify_document(name, text, rules=rules, **(jev_options or {}))
    except Exception as error:  # Classification problems become review items, not crashes.
        return {"role": "supporting", "type": None, "method": "jev", "review": str(error)}


def _is_pdf(content):
    return content[:5] == b"%PDF-"


def upload_filename(name):
    """Clean upload name: no accents (PJe garbles them), no unsafe characters, one space between words."""
    stem = Path(name).stem
    folded = unicodedata.normalize("NFKD", stem).encode("ascii", "ignore").decode()
    folded = re.sub(r'[\x00-\x1f<>:"/\\|?*#%&]', " ", folded)
    return f"{re.sub(r'[ _]{2,}', ' ', folded).strip() or 'documento'}.pdf"


class CanonicalNames:
    """Unique upload names for one run.

    PJe identifies a draft's documents by file name, and cleaning (accents, case, symbols) can turn two different files
    into one name: "Ã.pdf" and "a.pdf" both read as "A.pdf" to PJe. The first keeps the clean name; a later file
    whose name collides (compared as PJe compares, case- and accent-insensitive) gets a short content-hash suffix.
    """

    def __init__(self):
        self.taken = set()

    def claim(self, name, sha256):
        stem, candidate, length = Path(name).stem, name, 8
        while candidate.casefold() in self.taken:
            candidate, length = f"{stem}-{sha256[:length]}.pdf", length + 4
        self.taken.add(candidate.casefold())
        return candidate


def _label(name):
    """The descriptive part of a clean upload name, without the folder's own leading number: our NN replaces it."""
    stem = Path(upload_filename(name)).stem
    return re.sub(r"^\d+\s*[-.]\s*", "", stem).strip() or "documento"


def assemble(store, rows, *, write=True):
    """Write the run's documents/ folder from the chosen rows and return the payload's `documents`.

    `write=False` only works out (and hash-checks) what the assembly would be, leaving the run untouched.

    Filing order is explicit: the main petition is 01, then each supporting file in folder order, each volume of a
    split PDF with its own number (`03-LAUDOS-vol-01.pdf`). Names are unique within the run (see CanonicalNames). Each
    file is copied from the run's work area only after its content matches the reviewed `uploadSha256`. The manifest
    records what every assembled file is and which review row it came from; the payload names the files relative to
    the run, so the run stays valid wherever it is copied.
    """
    if write:
        store.delete_tree(DOCUMENTS)
        store.delete(MANIFEST)
    main = next(row for row in rows if row["role"] == "main")
    ordered = [main, *(row for row in rows if row["role"] == "supporting")]
    names, entries = CanonicalNames(), []
    for row in ordered:
        parts = row.get("parts") or []
        description = row.get("description") or ("Petição inicial" if row["role"] == "main" else Path(row["name"]).stem)
        for index, item in enumerate(parts or [row], 1):
            content = store.read_bytes(item["path"])
            if _sha256(content) != item["uploadSha256"]:
                raise ValueError(f"Document changed: {row['name']}")
            label = f"{_label(row['name'])}-vol-{index:02}" if parts else _label(row["name"])
            name = names.claim(f"{len(entries) + 1:02}-{label}.pdf", item["uploadSha256"])
            if write:
                store.write_bytes(f"{DOCUMENTS}/{name}", content)
            entry = {
                "path": f"{DOCUMENTS}/{name}",
                "type": row["type"],
                "description": f"{description} - volume {index}/{len(parts)}" if parts else description,
                "sha256": item["uploadSha256"],
            }
            if parts:
                entry["number"] = f"{index}/{len(parts)}"
            entries.append((entry, row, len(content), item.get("inputSha256") or row.get("sha256")))
    documents = [entry for entry, *_ in entries]
    if not write:
        return {"main": documents[0], "supporting": documents[1:]}
    store.write_json(
        MANIFEST,
        {
            "version": 1,
            "assembledAt": now(),
            "documents": [
                {
                    **entry,
                    "role": row["role"],
                    "size": size,
                    "reviewId": row["id"],
                    "sourceName": row["name"],
                    "inputSha256": input_sha256,
                    "inbox": row.get("inbox"),
                    "derivedBy": row.get("derivedBy"),
                    "origin": row.get("origin") or {},
                }
                for entry, row, size, input_sha256 in entries
            ],
        },
    )
    return {"main": documents[0], "supporting": documents[1:]}


def _prepare_row(entry, index, store, unlabeled_petition, seen_hashes, profile, jev_options=None):
    """Convert, classify and split one inbox file in the run's work area; the row names its files relative to the run.

    `sha256` is the inbox hash taken when the file was copied in; every derived file (conversion, volumes) records it
    as `inputSha256`, so each uploaded PDF traces back to the file and origin it came from.
    """
    sha256 = entry["sha256"]
    content = store.read_bytes(entry["path"])
    if _sha256(content) != sha256:
        raise ValueError(f"Inbox file changed after it was copied: {entry['name']}")
    source_path, derived_by, conversion_error = entry["path"], "copy", None
    if Path(entry["name"]).suffix.lower() in IMAGE_SUFFIXES:
        try:
            target = store.workspace(f"{WORK}/converted/{sha256}") / f"{Path(entry['name']).stem}.pdf"
            source_path = store.name_of(image_to_pdf(store.local_path(entry["path"]), target, PJE_LIMIT))
            content, derived_by = store.read_bytes(source_path), "image-to-pdf"
        except Exception as error:  # an unreadable or unsupported image becomes a review item
            conversion_error = f"Image conversion failed: {error}"
    ambiguous = document_kind(entry["name"], profile.document_rules) in ("decision", "medical", "unknown")
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
        classification = _safe_classify(
            entry["name"], analysis.get("markdown") or "", jev_options, profile.document_rules
        )
    if (
        classification.get("review")
        and ambiguous
        and len(content) <= 2_000_000
        and _is_pdf(content)
        and (analysis.get("textCharacters") or 0) < 80
    ):
        analysis = analyze_document(content, entry["name"], ocr=True)
        classification = _safe_classify(
            entry["name"], analysis.get("markdown") or "", jev_options, profile.document_rules
        )
    row = {
        "id": f"{sha256[:12]}:{index}",
        "name": entry["name"],
        "path": str(store.local_path(source_path)),
        "sha256": sha256,
        "inbox": entry["path"],
        "origin": entry.get("origin") or {},
        "derivedBy": derived_by,
        "pages": analysis.get("pageCount"),
        "textCharacters": analysis.get("textCharacters") or 0,
        **apply_supporting_fallback(_portal_types(classification, profile), profile.uncertain_type),
    }
    if conversion_error:
        row["review"] = conversion_error
    if sha256 in seen_hashes:
        row["review"] = "Duplicate file contents in folder"
    seen_hashes.add(sha256)
    path = Path(row["path"])
    if path.suffix.lower() != ".pdf":
        if path.suffix.lower() in (".doc", ".docx"):
            try:
                converted = store.workspace(f"{WORK}/converted/{sha256}") / f"{Path(entry['name']).stem}.pdf"
                row["path"] = str(convert_word_to_pdf(path, converted))
                row["derivedBy"] = "word-to-pdf"
            except Exception as error:
                row["review"] = f"PDF conversion failed: {error}"
        elif not conversion_error:
            row["review"] = "Document is not a PDF, an image or a convertible Word file"
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
                    row["parts"] = split_pdf(row["path"], store.workspace(f"{WORK}/split/{sha256}"))
                    for part in row["parts"]:
                        part["uploadSha256"] = _sha256(Path(part["path"]).read_bytes())
                        part["inputSha256"] = sha256
                    row["review"] = "; ".join(
                        filter(None, [row.get("review"), "Confirm split PDF page ranges before upload"])
                    )
                except Exception as error:
                    row["review"] = f"PDF splitting needs review: {error}"
    row["path"] = store.name_of(row["path"])
    for part in row.get("parts") or []:
        part["path"] = store.name_of(part["path"])
    return row


def _void_assembly(store):
    """Drop the payload, its approval and the assembled documents: whatever comes next must be reviewed anew."""
    store.delete(PAYLOAD)
    revoke_approval(store)
    store.delete(MANIFEST)
    store.delete_tree(DOCUMENTS)


def _require_no_draft(store):
    if store.exists(CHECKPOINT):
        raise ValueError("This run already has a PJe draft, so it cannot be prepared again; use a new runId")


def prepare_folder(source, facts, store, *, profile, jev=True):
    """The filing's one entry point: copy an input folder (see contract.py) into the run's inbox, then prepare it."""
    _require_no_draft(store)
    fill_from_folder(store, source)
    return prepare_inbox(store, facts, profile=profile, jev=jev)


def prepare_inbox(store, facts, *, profile, jev=True):
    """Convert, split and classify the files in the run's inbox, and write review.json (and, when nothing is left to
    review, the assembly and an unapproved payload). Reads nothing outside the run. `jev=False` keeps document text
    off the network.
    """
    jev_options = None if jev else {"openrouter_key": "", "api_key": ""}
    _require_no_draft(store)
    _void_assembly(store)
    store.delete_tree(WORK)
    inbox = read_inbox(store)
    files = inbox["files"]
    if not files:
        raise ValueError("The inbox has no files")
    editable = [entry for entry in files if re.search(r"\.docx?$", entry["name"], re.I)]
    unlabeled = (
        editable[0]
        if len(editable) == 1
        and not any(document_kind(entry["name"], profile.document_rules) == "petition" for entry in files)
        else None
    )
    seen = set()
    rows = [
        _prepare_row(entry, index, store, unlabeled, seen, profile, jev_options) for index, entry in enumerate(files)
    ]

    mains = [row for row in rows if row["role"] == "main"]
    review = [{"id": row["id"], "name": row["name"], "reason": row["review"]} for row in rows if row.get("review")]
    if len(mains) != 1:
        review.append({"name": "petition", "reason": f"Expected one main petition, found {len(mains)}"})
    candidates = {"claimValues": [], "cpfs": [], "representation": []}
    if len(mains) == 1 and mains[0].get("uploadSha256"):
        candidates["claimValues"], candidates["cpfs"], candidates["representation"] = _petition_candidates(
            store.local_path(mains[0]["path"])
        )
        claim_value = (facts.get("characteristics") or {}).get("claimValue")
        if claim_value and candidates["claimValues"] and claim_value not in candidates["claimValues"]:
            review.append({"name": "claimValue", "reason": "Case fact differs from the petition's stated claim value"})
        if facts.get("authorCpf") and candidates["cpfs"] and _digits(facts["authorCpf"]) not in candidates["cpfs"]:
            review.append(
                {"name": "authorCpf", "reason": "Author CPF was not found among the petition's formatted CPFs"}
            )
    try:
        require_facts(facts, profile.defaults())
        for reason in representation_problems(facts, candidates["cpfs"], candidates["representation"]):
            review.append({"name": "legal representative", "reason": reason})
    except ValueError as error:
        review.append({"name": "case facts", "reason": str(error)})
    if not review:
        try:
            payload = build_payload(facts, assemble(store, rows), profile.defaults())
            store.write_json(PAYLOAD, payload)  # written, but not approved: filing_approve decides that
        except ValueError as error:
            _void_assembly(store)
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
        "files": rows,
        "candidates": candidates,
        "review": review,
        "ready": not review,
        "fallbacks": fallbacks,
    }
    store.write_json(REVIEW, report)
    return {
        "ready": report["ready"],
        "review": review,
        "fileCount": len(rows),
        "fallbackCount": len(fallbacks),
        "payloadWritten": store.exists(PAYLOAD),
    }


def _verify_row(store, row, profile):
    if row["role"] not in ("main", "supporting"):
        raise ValueError(f"Invalid role for {row['name']}")
    if row["role"] == "main" and row.get("type") != "Petição inicial":
        raise ValueError(f"Invalid main type for {row['name']}")
    parts = row.get("parts")
    if parts is not None and (not isinstance(parts, list) or not parts):
        raise ValueError(f"Invalid PDF volumes: {row['name']}")
    if row["role"] == "main" and parts:
        raise ValueError(f"Main petition must be one PDF: {row['name']}")
    if row["role"] == "supporting" and row.get("type") not in profile.supporting_types:
        raise ValueError(f"Invalid PJe type for {row['name']}")
    if row.get("review") or not row.get("type") or not row.get("uploadSha256"):
        raise ValueError(f"Document needs review: {row['name']}")
    content = store.read_bytes(row["path"])
    if _sha256(content) != row["uploadSha256"]:
        raise ValueError(f"Document changed: {row['name']}")
    if (not parts and len(content) > PJE_LIMIT) or not _is_pdf(content):
        raise ValueError(f"Invalid PDF: {row['name']}")
    for part in parts or []:
        part_content = store.read_bytes(part["path"])
        if len(part_content) > PJE_LIMIT or not _is_pdf(part_content) or _sha256(part_content) != part["uploadSha256"]:
            raise ValueError(f"Split PDF changed or is invalid: {part['path']}")


def _checked_override(file_id, choice):
    """A reviewer's choice for one file: only role, type and description, each a nonempty string."""
    if not isinstance(choice, dict):
        raise ValueError(f"Override for {file_id} must be an object")
    unknown = sorted(set(choice) - OVERRIDE_KEYS)
    if unknown:
        raise ValueError(
            f"Override for {file_id} names fields that cannot be changed: {', '.join(unknown)} "
            "(only role, type and description)"
        )
    for key, value in choice.items():
        if not isinstance(value, str) or not value.strip() or len(value) > 200:
            raise ValueError(f"Override {key} for {file_id} must be a nonempty text of at most 200 characters")
    if "role" in choice and choice["role"] not in ("main", "supporting", "skip"):
        raise ValueError(f"Override role for {file_id} must be main, supporting or skip")
    return {key: value.strip() for key, value in choice.items()}


def finalize_review(store, facts, overrides=None, *, profile):
    """Apply the reviewer's explicit choices, assemble documents/ and write payload.json (unapproved) in the run."""
    overrides = overrides or {}
    report = store.read_json(REVIEW)
    unknown = sorted(set(overrides) - {row["id"] for row in report["files"]})
    if unknown:
        raise ValueError(f"Overrides name files that are not in the review: {', '.join(unknown)}")
    choices = {file_id: _checked_override(file_id, choice) for file_id, choice in overrides.items()}
    # Once a draft exists its documents are on PJe under their assembled names: only case facts may still change.
    drafted = store.exists(CHECKPOINT)
    previous = (store.read_json(PAYLOAD).get("documents") if store.exists(PAYLOAD) else None) if drafted else None
    if drafted:
        revoke_approval(store)
    else:
        _void_assembly(store)
    rows = []
    for original in report["files"]:
        choice = choices.get(original["id"])
        row = {**original, **(choice or {})}
        if choice:
            row.pop("review", None)
        if row["role"] == "skip":
            continue
        _verify_row(store, row, profile)
        rows.append(row)
    mains = [row for row in rows if row["role"] == "main"]
    if len(mains) != 1:
        raise ValueError(f"Expected one main petition, found {len(mains)}")
    claim_values, cpfs, signals = _petition_candidates(store.local_path(mains[0]["path"]))
    if claim_values and (facts.get("characteristics") or {}).get("claimValue") not in claim_values:
        raise ValueError("Case claimValue differs from the selected petition's stated value")
    if cpfs and _digits(facts.get("authorCpf")) not in cpfs:
        raise ValueError("Author CPF was not found among the selected petition's formatted CPFs")
    build_payload(facts, {}, profile.defaults())
    problems = representation_problems(facts, cpfs, signals)
    if problems:
        raise ValueError("; ".join(problems))  # facts problems surface before any file is assembled
    if drafted:
        documents = assemble(store, rows, write=False)
        if documents != previous:
            raise ValueError(
                "This run already has a PJe draft, so its documents can no longer change (only case facts can); "
                "use a new runId"
            )
    else:
        documents = assemble(store, rows)
    store.write_json(PAYLOAD, build_payload(facts, documents, profile.defaults()))
    return {"ready": True, "documentCount": 1 + len(documents["supporting"])}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Prepare a PJe filing payload from an input folder")
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument(
        "--from", dest="source", required=True, type=Path, help="Input folder (files + optional sources.json)"
    )
    prepare.add_argument("--facts", required=True, type=Path)
    prepare.add_argument("--out", required=True, type=Path)
    prepare.add_argument("--no-jev", action="store_true", help="Keep document text off the network")
    finalize = commands.add_parser("finalize")
    finalize.add_argument("--run", required=True, type=Path, help="The run folder prepare wrote (holds review.json)")
    finalize.add_argument("--facts", required=True, type=Path)
    finalize.add_argument("--overrides", type=Path)
    for command in (prepare, finalize):
        command.add_argument("--profile", required=True, help="Filing profile name, e.g. victor")
    approval = commands.add_parser("approve", help="Record the user's approval of RUN_DIR/payload.json")
    approval.add_argument("--run", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.command == "approve":
        print(json.dumps(approve(LocalRunStore(args.run)), ensure_ascii=False, indent=2))
        return
    profile = load_profile(args.profile)
    facts = json.loads(args.facts.read_text(encoding="utf-8"))
    if args.command == "prepare":
        store = LocalRunStore(args.out).ensure()
        result = prepare_folder(args.source.resolve(), facts, store, profile=profile, jev=not args.no_jev)
    else:
        overrides = json.loads(args.overrides.read_text(encoding="utf-8")) if args.overrides else {}
        result = finalize_review(LocalRunStore(args.run), facts, overrides, profile=profile)
    print(json.dumps(result, ensure_ascii=False, indent=2))
