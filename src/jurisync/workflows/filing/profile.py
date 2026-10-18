"""Customer filing profiles: an office's defaults and conventions, kept as data and selected by name.

A profile is `customers/<name>/filing-profile.json` (profile `<name>`), or a portal variant
`customers/<name>/filing-profile.<variant>.json` (profile `<name>-<variant>`, e.g. `victor-trf1`). Shared code only reads that file; it never imports customer code.
It holds only what belongs to the protocol (parties every filing of the office has, characteristics, references, type
rules). Case data, including any extra attorney, comes with each case's facts; the attorney logged in to PJe is on
every draft by default.
"""

import copy
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from jurisync.paths import REPO_ROOT

PROFILE_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
PROFILE_FILE = "filing-profile.json"
# PJe document types as the TRF5 portal names them. Classification speaks this vocabulary; a profile for another portal
# maps it to that portal's labels (typeMap) and lists the labels it accepts for supporting files (supportingTypes).
CANONICAL_SUPPORTING_TYPES = (
    "Planilha",
    "Outros Documentos",
    "Documento de Identificação",
    "Processo Administrativo",
    "Documento Comprobatório",
    "Carta de Indeferimento",
    "Carta de Concessão",
    "Laudo Médico",
)


@dataclass(frozen=True)
class Profile:
    name: str
    directory: Path
    portal: str
    uncertain_type: str
    defaults_template: dict
    document_rules: tuple | None = None
    references: dict | None = None
    description: str = ""
    supporting_types: frozenset = frozenset(CANONICAL_SUPPORTING_TYPES)
    type_map: dict | None = None

    def portal_type(self, document_type):
        """The portal's label for a classification type (unchanged when the profile does not map it)."""
        return (self.type_map or {}).get(document_type, document_type)

    def defaults(self):
        """The office defaults for a payload."""
        return {"portal": self.portal, **copy.deepcopy(self.defaults_template)}

    def reference(self, key):
        """Absolute path of a reference file the profile names, kept inside the profile's own folder."""
        relative = (self.references or {}).get(key)
        if not relative:
            return None
        path = (self.directory / relative).resolve()
        if self.directory.resolve() not in path.parents:
            raise ValueError(f"Profile reference {key} points outside the profile folder")
        return path


def _profile_files():
    root = Path(os.environ.get("JURISYNC_PROFILES_DIR") or REPO_ROOT / "customers")
    return sorted([*root.glob(f"*/{PROFILE_FILE}"), *root.glob("*/filing-profile.*.json")])


def _profile_name(path):
    if path.name == PROFILE_FILE:
        return path.parent.name
    return f"{path.parent.name}-{path.name.removeprefix('filing-profile.').removesuffix('.json')}"


def profile_names():
    return [_profile_name(path) for path in _profile_files()]


def load_profile(name):
    if not isinstance(name, str) or not PROFILE_NAME.fullmatch(name):
        raise ValueError("Profile names use lowercase letters, digits and hyphens")
    path = next((file for file in _profile_files() if _profile_name(file) == name), None)
    if path is None:
        raise ValueError(f"No filing profile named {name}; available: {', '.join(profile_names()) or 'none'}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if "folderPatterns" in data:
        raise ValueError(
            f"Profile {name} has folderPatterns: choosing a source folder belongs to the source's exporter, "
            "not to the filing profile"
        )
    defaults = data.get("defaults") or {}
    if "attorney" in defaults:
        raise ValueError(
            f"Profile {name} has defaults.attorney: extra attorneys come with each case's facts (attorneys), and the "
            "attorney logged in to PJe is on the draft by default"
        )
    if not isinstance(defaults.get("passive"), dict):
        raise ValueError(f"Profile {name} needs defaults.passive")
    if not data.get("portal"):
        raise ValueError(f"Profile {name} needs a portal")
    rules = data.get("documentRules")
    if rules is not None:
        for pair in rules:
            if not (isinstance(pair, list) and len(pair) == 2):
                raise ValueError("documentRules entries are [pattern, kind] pairs")
            re.compile(pair[0])
        rules = tuple((pattern, kind) for pattern, kind in rules)
    supporting = data.get("supportingTypes", list(CANONICAL_SUPPORTING_TYPES))
    if not isinstance(supporting, list) or not supporting or not all(isinstance(item, str) for item in supporting):
        raise ValueError(f"Profile {name} supportingTypes must be a nonempty list of PJe labels")
    type_map = data.get("typeMap") or {}
    if not isinstance(type_map, dict) or any(value not in supporting for value in type_map.values()):
        raise ValueError(f"Profile {name} typeMap must map classification types to labels in supportingTypes")
    uncertain = data.get("uncertainType") or "Outros Documentos"
    if uncertain not in supporting:
        raise ValueError(f"Profile {name} uncertainType must be one of its supportingTypes")
    return Profile(
        name=name,
        directory=path.parent,
        portal=data["portal"],
        uncertain_type=uncertain,
        defaults_template=defaults,
        document_rules=rules,
        references=data.get("references"),
        description=data.get("description") or "",
        supporting_types=frozenset(supporting),
        type_map=type_map,
    )
