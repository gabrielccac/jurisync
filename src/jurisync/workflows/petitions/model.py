"""A petition model (blocks, dossier fields, gender forms, rules) and one author's view of a dossier."""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

PLACEHOLDER = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")
SHARED = ("viagem", "processo", "peca", "reu", "escritorio", "doc", "imagem", "calc")
AUTHOR_PARTS = ("autor", "representante", "processo", "peca", "doc", "imagem", "calc")


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


@dataclass
class PetitionModel:
    meta: dict
    fields: dict
    genders: dict
    verbs: list
    decisions: list
    rules: list = field(default_factory=list)

    @classmethod
    def load(cls, folder):
        folder = Path(folder)
        dossier = _read(folder / "dossie.json")
        genders = _read(folder / "generos.json")
        decisions = _read(folder / "pendencias.json")
        rules_path = folder / "regras.json"
        return cls(
            meta=_read(folder / "modelo.json"),
            fields={item["campo"]: item for item in dossier["campos"]},
            genders=genders["campos"],
            verbs=genders.get("concordancia_verbal", []),
            decisions=decisions if isinstance(decisions, list) else decisions.get("pendencias", []),
            rules=_read(rules_path)["regras"] if rules_path.exists() else [],
        )

    @property
    def blocks(self):
        return {block["id"]: block for section in self.meta["secoes"] for block in section["blocos"]}

    @property
    def section_titles(self):
        return {section["id"]: section["titulo"] for section in self.meta["secoes"]}


class AuthorCase:
    """One action: what is common to the trip plus what belongs to one passenger."""

    def __init__(self, dossier, author_index=0):
        authors = dossier.get("autores") or []
        if not 0 <= author_index < len(authors):
            raise ValueError(f"Dossier has no author at position {author_index}")
        self.dossier = dossier
        self.index = author_index
        self.author = authors[author_index]
        self.calculated = {}

    def get(self, name):
        if name == "peca.cenario":
            return self._lookup("peca.cenario_referencia") or self._lookup(name)
        return self._lookup(name)

    def _lookup(self, name):
        part, _, key = name.partition(".")
        if not key:
            return None
        if part == "calc" and key in self.calculated:
            return self.calculated[key]
        own = self.author.get(part)
        if isinstance(own, dict) and own.get(key) not in (None, ""):
            return own[key]
        if part in SHARED:
            shared = self.dossier.get(part)
            if isinstance(shared, dict) and shared.get(key) not in (None, ""):
                return shared[key]
        return None

    def not_available(self):
        """Fields recorded as not held by the client, with the reason and who recorded them."""
        entries = {}
        for entry in self.dossier.get("nao_possui") or []:
            if entry.get("autor") in (None, self.index):
                entries[entry["campo"]] = entry
        return entries

    def other_people(self):
        """Names, first names and CPFs of the other passengers, which must not appear in this author's petition."""
        own = set(str((self.author.get("autor") or {}).get("nome") or "").casefold().split())
        values = []
        for position, other in enumerate(self.dossier.get("autores") or []):
            if position == self.index:
                continue
            person = other.get("autor") or {}
            name = str(person.get("nome") or "")
            first = name.split()[0] if name.split() else ""
            values += [name, person.get("nome_curto"), person.get("cpf"), first if first.casefold() not in own else ""]
        return [value for value in values if value]


def placeholders(text):
    return PLACEHOLDER.findall(text or "")
