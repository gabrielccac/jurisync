"""The author's legal representative, linked through the active pole's "Procurador | Terceiro Vinculado".

Seen on TRF1 (read-only, draft 15508678, 2026-10-19), up to the registry lookup: the toolbar opens
`mpAssociarRepresentanteProcesso` with a "Tipo de representação" select (ADVOGADO, CURADOR, REPRESENTANTE, TUTOR...);
choosing one renders `preCadastroPessoaFisicaRepresentanteForm` (Física/Jurídica, CPF, Pesquisar); Pesquisar fills
the registry name (`dsNomeCivil`) and shows Confirmar. What follows Confirmar (the represented party, the address,
Vincular) was not opened, so that part checks every element it needs and stops with what it found when one is missing;
the portal lists the step as unverified until a real run confirms it. Afterwards the Partes tab must show the
representative's CPF in the active pole, or the step fails.
"""

import re

from bs4 import BeautifulSoup

from jurisync.connectors.pje import jsf
from jurisync.connectors.pje.filing.parsing import has_identifier, name_matches
from jurisync.connectors.pje.filing.payload import address_cep, masked_cpf
from jurisync.connectors.pje.parsers import jsf_data

# PJe's "Tipo de representação" for each kind the case facts allow.
REPRESENTATION_TYPES = {
    "mae": "REPRESENTANTE",
    "pai": "REPRESENTANTE",
    "procurador": "REPRESENTANTE",
    "outro": "REPRESENTANTE",
    "tutor": "TUTOR",
    "curador": "CURADOR",
}
TYPE_SELECT = "formTipoRepresentacao:comboRepresentacao"
PERSON_FORM = "preCadastroPessoaFisicaRepresentanteForm"


def _messages(html):
    return [
        node.get_text(" ", strip=True) for node in BeautifulSoup(html, "html.parser").select(".rich-messages-label")
    ]


def _forms_seen(soup):
    """A short description of the forms and buttons on a page, for an error that must say where the flow stopped."""
    forms = [form.get("id") for form in soup.find_all("form") if form.get("id")]
    buttons = [
        (button.get("value") or button.get_text(" ", strip=True))[:30]
        for button in soup.find_all(["input", "a", "button"])
        if button.get("type") in ("button", "submit") or button.name in ("a", "button")
    ]
    return f"forms {forms[:12]}, buttons {[label for label in buttons if label][:15]}"


class RepresentativeMixin:
    def representative_listed(self, html, representative):
        """Whether the active pole already shows this representative (by CPF, with the name or its initials)."""
        active = BeautifulSoup(html, "html.parser").find(id="poloAtivoDatalistDiv")
        if active is None:
            raise RuntimeError("PJe did not render the active-party list")
        text = active.get_text(" ", strip=True)
        return has_identifier(text, representative["cpf"])

    def add_representative(self, draft_url, representative, author_name, author_cpf, panel=None):
        html = panel if panel is not None else self.party_panel(draft_url)
        if self.representative_listed(html, representative):
            return {"alreadyAdded": True, "name": representative["name"]}
        kind = REPRESENTATION_TYPES[representative["kind"]]
        soup = BeautifulSoup(html, "html.parser")
        toolbar = soup.find("form", id="formVincularPoloAtivo")
        add = toolbar.find(id="addRepresentanteA") if toolbar else None
        if add is None or add.find_parent(attrs={"onclick": True}) is None:
            raise RuntimeError("PJe did not render the active pole's 'Procurador | Terceiro Vinculado' action")
        container, action = jsf.ajax_event(add.find_parent(attrs={"onclick": True}))
        html = self.ajax(
            self.update_url, jsf.view_state(html), jsf.form_fields(html, "formVincularPoloAtivo"), container, action
        )
        soup = BeautifulSoup(html, "html.parser")
        select = soup.find("select", attrs={"name": TYPE_SELECT})
        if select is None:
            raise RuntimeError("PJe did not render the representation type select")
        _, value, _ = jsf.select_option(soup, TYPE_SELECT, kind)
        fields = jsf.form_fields(html, "formTipoRepresentacao")
        fields[TYPE_SELECT] = value
        container, action = jsf.ajax_event(select)
        html = self.ajax(self.update_url, jsf.view_state(html), fields, container, action)
        html, fields = self._representative_lookup(html, representative)
        soup = BeautifulSoup(html, "html.parser")
        confirm = soup.find("input", attrs={"name": PERSON_FORM + ":btnConfirmarCadastro"})
        if confirm is None:
            raise RuntimeError("PJe did not offer Confirmar after the representative's CPF lookup")
        # The lookup answers are partial pages (no <form>): the fields gathered along the way go with Confirmar.
        container, action = jsf.ajax_event(confirm)
        html = self.ajax(self.update_url, jsf.view_state(html), fields, container, action)
        linked = self._link_representative(html, representative, author_name)
        listed = self.party_panel(draft_url)
        if not self.representative_listed(listed, representative):
            raise RuntimeError(
                "PJe did not list the legal representative in the active pole after Vincular"
                + (f": {'; '.join(_messages(linked))}" if _messages(linked) else "")
            )
        return {"alreadyAdded": False, "name": representative["name"], "type": kind}

    def _representative_lookup(self, html, representative):
        """Física, Brazilian, the masked CPF and Pesquisar; the registry name must match the case facts."""
        soup = BeautifulSoup(html, "html.parser")
        person = soup.find("input", attrs={"name": PERSON_FORM + ":tipoPessoaDecoration:tipoPessoa", "value": "F"})
        if person is None:
            raise RuntimeError("PJe did not render the representative's person form")
        fields = jsf.form_fields(html, PERSON_FORM)
        fields[person["name"]] = "F"
        container, action = jsf.ajax_event(person)
        html = self.ajax(self.update_url, jsf.view_state(html), fields, container, action)
        soup = BeautifulSoup(html, "html.parser")
        fields.update({key: value for key, value in jsf_data(html)[1].items() if key.startswith(PERSON_FORM + ":")})
        brazilian = soup.find("input", attrs={"name": re.compile(r"isBrasileiro"), "value": "true"})
        if brazilian is not None:
            fields[brazilian["name"]] = "true"
        cpf = next(
            (field for field in soup.find_all("input") if (field.get("name") or "").endswith("Representante_nrCPF")),
            None,
        )
        search = soup.find("input", attrs={"name": PERSON_FORM + ":pesquisarDocumentoPrincipal"})
        if cpf is None or search is None:
            raise RuntimeError("PJe did not render the representative's CPF search")
        fields[cpf["name"]] = masked_cpf(representative["cpf"])
        container, action = jsf.ajax_event(search)
        html = self.ajax(self.update_url, jsf.view_state(html), fields, container, action)
        name = BeautifulSoup(html, "html.parser").find("input", attrs={"name": PERSON_FORM + ":dsNomeCivil"})
        if name is None:
            detail = "; ".join(_messages(html)) or "no registry name returned"
            raise RuntimeError(f"PJe did not find the representative's CPF: {detail}")
        if not name_matches(name.get("value", ""), representative["name"]):
            raise RuntimeError("The representative's CPF belongs to another name in PJe's registry")
        fields.update({key: value for key, value in jsf_data(html)[1].items() if key.startswith(PERSON_FORM + ":")})
        return html, fields

    def _link_representative(self, html, representative, author_name):
        """After Confirmar: tie the representative to the author, pick the address, Vincular (not yet seen live)."""
        soup = BeautifulSoup(html, "html.parser")
        link = next(
            (
                button
                for button in soup.find_all("input", attrs={"type": re.compile("button|submit")})
                if re.search(r"vincular", button.get("value", ""), re.I) and "A4J" in (button.get("onclick") or "")
            ),
            None,
        )
        if link is None:
            raise RuntimeError(f"PJe offered no Vincular for the representative after Confirmar ({_forms_seen(soup)})")
        form = link.find_parent("form")
        if form is None or not form.get("id"):
            raise RuntimeError("The representative's Vincular is outside a form")
        fields = jsf.form_fields(html, form["id"])
        fields.update({key: value for key, value in jsf_data(html)[1].items() if key.startswith(form["id"] + ":")})
        self._choose_represented_party(form, fields, author_name)
        if representative.get("address"):
            self._choose_representative_address(form, representative["address"])
        container, action = jsf.ajax_event(link)
        return self.ajax(self.update_url, jsf.view_state(html), fields, container, action)

    @staticmethod
    def _choose_represented_party(form, fields, author_name):
        """When PJe asks which party is represented (a select or checkboxes), choose the author and only the author."""
        for select in form.find_all("select"):
            options = [
                option for option in select.find_all("option") if name_matches(option.get_text(" "), author_name)
            ]
            if options:
                if len(options) != 1:
                    raise RuntimeError("PJe lists the author more than once as the represented party")
                fields[select["name"]] = options[0].get("value", "")
                return
        boxes = [
            box
            for box in form.find_all("input", attrs={"type": "checkbox"})
            if box.find_parent("tr") is not None and name_matches(box.find_parent("tr").get_text(" "), author_name)
        ]
        if len(boxes) > 1:
            raise RuntimeError("PJe lists the author more than once as the represented party")
        if boxes:
            fields[boxes[0]["name"]] = boxes[0].get("value") or "on"

    def _choose_representative_address(self, form, address):
        """Use an address PJe already has for the representative (registering a new one is not automated)."""
        cep = address_cep(address)
        try:
            rows = self._address_rows(str(form), cep)
        except RuntimeError:
            raise RuntimeError(
                "PJe showed no address list for the representative; add the address by hand in PJe"
            ) from None
        if len(rows) != 1:
            raise RuntimeError(
                f"PJe has {len(rows)} addresses with CEP {cep} for the representative; choose it by hand in PJe"
            )
        if not rows[0][1]:
            raise RuntimeError("The representative's address is not selected; choose it by hand in PJe")
