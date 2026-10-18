"""Payload validation and value formats; runs before any network call."""

import hashlib
import re
from pathlib import Path

PDF_LIMIT = 10_000_000
SHA256 = re.compile(r"[0-9a-f]{64}")


def masked_cpf(value):
    digits = re.sub(r"\D", "", value)
    if len(digits) != 11:
        raise ValueError("CPF must have 11 digits")
    return f"{digits[:3]}.{digits[3:6]}.{digits[6:9]}-{digits[9:]}"


def address_cep(address):
    if not isinstance(address, dict):
        raise ValueError("author.address must be an object")
    raw = str(address.get("cep", "")).strip()
    if not re.fullmatch(r"\d{5}-?\d{3}", raw):
        raise ValueError("author.address.cep must have 8 digits")
    cep = raw.replace("-", "")
    return f"{cep[:5]}-{cep[5:]}"


def claim_amount(value):
    return re.sub(r"^R\$\s*", "", value.strip())


def payload_subjects(payload):
    if "subject" in payload and "subjects" in payload:
        raise ValueError("Use subject or subjects, not both")
    subjects = payload.get("subjects", [payload["subject"]] if "subject" in payload else [])
    if not isinstance(subjects, list) or not subjects:
        raise ValueError("subjects must be a nonempty list")
    codes = []
    for index, subject in enumerate(subjects):
        if not isinstance(subject, dict):
            raise ValueError(f"subjects[{index}] must be an object")
        for field in ("search", "code", "name"):
            if not isinstance(subject.get(field), str) or not subject[field].strip():
                raise ValueError(f"subjects[{index}].{field} is required")
        codes.append(subject["code"])
    if len(codes) != len(set(codes)):
        raise ValueError("subjects must have unique codes")
    return subjects


def validate_characteristics(characteristics):
    if not isinstance(characteristics, dict):
        raise ValueError("characteristics must be an object")
    claim_value = characteristics.get("claimValue")
    if not isinstance(claim_value, str) or not re.fullmatch(r"\d+(?:\.\d{3})*,\d{2}", claim_value):
        raise ValueError("claimValue must use Brazilian currency format, e.g. 1.000,00")
    for key in ("legalAid", "injunction"):
        if not isinstance(characteristics.get(key), bool):
            raise ValueError(f"{key} must be a boolean")
    priority_labels(characteristics)
    for key in ("digitalCourt", "secrecy"):
        if key in characteristics and not isinstance(characteristics[key], bool):
            raise ValueError(f"{key} must be a boolean")
    if characteristics.get("secrecy") is True:
        raise ValueError("secrecy true is not supported: set segredo de justiça by hand in PJe")
    return claim_value


def priority_labels(characteristics):
    """The priorities to have on the draft: one label, a list of labels (new form), or false/absent for none."""
    priority = characteristics.get("priority")
    if priority is None or priority is False:
        return []
    labels = [priority] if isinstance(priority, str) else priority
    if not isinstance(labels, list) or any(
        not isinstance(label, str) or not label.strip() or re.fullmatch(r"N[ÃA]O", label.strip(), re.I)
        for label in labels
    ):
        raise ValueError("priority must be a PJe option label, a list of labels, or false to omit it")
    return [label.strip() for label in labels]


def validate_fill_payload(payload):
    payload_subjects(payload)
    reference = (payload.get("classification") or {}).get("referenceProcess")
    if reference is not None and len(re.sub(r"\D", "", str(reference))) != 20:
        raise ValueError("classification.referenceProcess must be a CNJ number with 20 digits")
    roles = payload.get("partyRoles")
    if roles is not None and (
        not isinstance(roles, dict)
        or set(roles) - {"active", "passive"}
        or not all(isinstance(value, str) and value.strip() for value in roles.values())
    ):
        raise ValueError("partyRoles maps active/passive to PJe 'Tipo da Parte' labels, e.g. AGRAVANTE")
    author = payload.get("author") or {}
    required = (("authorCpf", payload.get("authorCpf")), ("author.name", author.get("name")))
    missing = [name for name, value in required if not value]
    if missing:
        raise ValueError(f"Missing draft payload fields: {', '.join(missing)}")
    if not isinstance(payload["authorCpf"], str):
        raise ValueError("authorCpf must be a string")
    masked_cpf(payload["authorCpf"])
    if author.get("address") is not None:
        address_cep(author["address"])
    representative = author.get("representative")
    if representative is not None:
        if not isinstance(representative, dict) or not all(representative.get(key) for key in ("cpf", "name", "kind")):
            raise ValueError("author.representative needs cpf, name and kind")
        masked_cpf(representative["cpf"])
        if representative.get("address") is not None:
            address_cep(representative["address"])
    if "attorney" in payload:
        raise ValueError("Use attorneys (a list of extra attorneys), not attorney: finalize the payload again")
    attorneys = payload.get("attorneys", [])
    if not isinstance(attorneys, list):
        raise ValueError("attorneys must be a list")
    for index, attorney in enumerate(attorneys):
        if not isinstance(attorney, dict) or not all(
            attorney.get(key) for key in ("cpf", "name", "oabNumber", "oabState")
        ):
            raise ValueError(f"attorneys[{index}] needs cpf, name, oabNumber and oabState")
        masked_cpf(attorney["cpf"])
    if "characteristics" in payload:
        validate_characteristics(payload["characteristics"])
    documents = payload.get("documents")
    if documents is not None:
        if not isinstance(documents, dict):
            raise ValueError("documents must be an object")
        if not documents.get("main", {}).get("path"):
            raise ValueError("documents.main.path is required")
        for item in (documents["main"], *documents.get("supporting", [])):
            source = Path(item.get("path", "")).expanduser()
            if source.suffix.lower() != ".pdf" or not source.is_file() or source.stat().st_size > PDF_LIMIT:
                raise ValueError(f"Document must be an existing PDF of at most 10 MB: {source}")
            approved_content(item)


def approved_content(document):
    """The document's bytes, read once and checked against the sha256 that was reviewed and approved.

    Payloads written before documents carried a sha256 cannot prove what was approved, so they are refused.
    """
    path = Path(document["path"]).expanduser()
    expected = document.get("sha256")
    if not isinstance(expected, str) or not SHA256.fullmatch(expected):
        raise ValueError(
            f"Document {path.name} has no sha256 in the payload (written before integrity checks): "
            "run pje_filing_build again and approve the new payload; a run that already has a draft needs a new runId"
        )
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != expected:
        raise ValueError(
            f"Document changed since approval: {path.name}. Run pje_filing_build again and get a new approval"
        )
    return content


def payload_documents(payload):
    documents = payload.get("documents") or {}
    return [documents["main"], *documents.get("supporting", [])] if documents.get("main") else []


def resolve_documents(payload, store):
    """A copy of the payload whose document paths are local files of the run.

    Payloads name documents relative to their run (`documents/01-...pdf`), so a run stays valid wherever it is kept.
    Absolute paths come from payloads written before runs were self-contained; they are refused rather than trusted,
    since they point outside the assembly that was approved.
    """
    documents = payload.get("documents")
    if not isinstance(documents, dict):
        return payload
    from jurisync.connectors.pje.filing.store import DOCUMENTS, run_name

    def resolve(item):
        name = item.get("path") if isinstance(item, dict) else None
        if not isinstance(name, str) or not name:
            raise ValueError("Every document needs a path")
        if Path(name).is_absolute() or name.startswith("~"):
            raise ValueError(
                f"Document path is absolute ({Path(name).name}): this payload was written before filing runs were "
                "self-contained. Run pje_filing_build again and approve the new payload; a run that already has a "
                "draft needs a new runId"
            )
        if not run_name(name).startswith(DOCUMENTS + "/"):
            raise ValueError(f"Document {name} is not in the run's {DOCUMENTS}/ folder")
        return {**item, "path": str(store.local_path(name))}

    resolved = {**documents}
    if documents.get("main") is not None:
        resolved["main"] = resolve(documents["main"])
    resolved["supporting"] = [resolve(item) for item in documents.get("supporting", [])]
    return {**payload, "documents": resolved}
