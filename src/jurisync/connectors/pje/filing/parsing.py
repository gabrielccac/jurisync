"""Small HTML and text matchers shared by the draft sections."""

import re

from bs4 import BeautifulSoup


def name_matches(actual, expected):
    import unicodedata

    def words(value):
        plain = "".join(
            char for char in unicodedata.normalize("NFD", value.lower()) if unicodedata.category(char) != "Mn"
        )
        return set(plain.split())

    return words(expected) <= words(actual)


def has_identifier(text, identifier):
    return re.sub(r"\D", "", identifier) in re.sub(r"\D", "", text)


NAME_PARTICLES = {"de", "da", "do", "das", "dos", "e"}


def initials(name):
    """'DAVI LEÃO JARDIM' -> 'DLJ' (sem acento, sem partículas como 'de', 'dos')."""
    import unicodedata

    plain = "".join(c for c in unicodedata.normalize("NFD", name) if unicodedata.category(c) != "Mn")
    return "".join(word[0] for word in plain.upper().split() if word.lower() not in NAME_PARTICLES)


def party_matches(text, expected_name, cpf=None):
    """A parte listada pelo PJe é a esperada?

    O TRF1 mostra partes menores de idade só pelas iniciais ("D. L. J. - CPF: ... (AUTOR)"): nesse caso o nome
    completo não aparece, e a parte é reconhecida pelo CPF junto com as iniciais do nome esperado.
    """
    if name_matches(text, expected_name):
        return True
    if not cpf or not has_identifier(text, cpf):
        return False
    shown = re.findall(r"\b(?:[A-ZÀ-Ý]\.\s*){2,}", text)
    return any(re.sub(r"[^A-Z]", "", group) == initials(expected_name) for group in shown)


def action_in_form(html, form_id, action_suffix):
    form = BeautifulSoup(html, "html.parser").find("form", id=form_id)
    if form is None:
        raise RuntimeError(f"PJe form {form_id} was not rendered")
    matches = [
        element
        for element in form.find_all(onclick=True)
        if f"'similarityGroupingId':'{form_id}:{action_suffix}'" in element["onclick"]
    ]
    if not matches:
        raise RuntimeError(f"PJe action {form_id}:{action_suffix} was not rendered")
    return matches[0]
