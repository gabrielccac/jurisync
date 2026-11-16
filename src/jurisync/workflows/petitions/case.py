"""One action of a case file: what is common to the trip plus what belongs to the authors of this action."""

SHARED = ("viagem", "processo", "peca", "reu", "escritorio", "doc", "imagem", "calc")


class AuthorCase:
    """The case file seen from one action. ``group`` lists the authors of a joint action (positions from 0)."""

    def __init__(self, dossier, author_index=0, defaults=None, group=None):
        authors = dossier.get("autores") or []
        group = [author_index] + [index for index in group or () if index != author_index]
        for index in group:
            if not isinstance(index, int) or not 0 <= index < len(authors):
                raise ValueError(f"O dossiê não tem o autor {index + 1 if isinstance(index, int) else index}")
        self.dossier = dossier
        self.index = author_index
        self.group = group
        self.joint = len(group) > 1
        self.author = authors[author_index]
        self.calculated = {}
        self.defaults = defaults or {}
        self.defaults_used = {}

    def members(self):
        return [AuthorCase(self.dossier, index, self.defaults) for index in self.group]

    def get(self, name):
        if name == "autor.numero":
            return "plural" if self.joint else self._lookup(name) or "singular"
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
        if name in self.defaults:
            self.defaults_used[name] = self.defaults[name]
            return self.defaults[name]
        return None

    def other_people(self):
        """Names, first names and CPFs of passengers outside this action: they must not appear in its petition."""
        authors = self.dossier.get("autores") or []
        own = set()
        for index in self.group:
            own |= set(str((authors[index].get("autor") or {}).get("nome") or "").casefold().split())
        values = []
        for position, other in enumerate(authors):
            if position in self.group:
                continue
            person = other.get("autor") or {}
            name = str(person.get("nome") or "")
            first = name.split()[0] if name.split() else ""
            values += [name, person.get("nome_curto"), person.get("cpf"), first if first.casefold() not in own else ""]
        values += [str(item) for item in self.dossier.get("terceiros") or [] if item]
        return [value for value in values if value]
