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
