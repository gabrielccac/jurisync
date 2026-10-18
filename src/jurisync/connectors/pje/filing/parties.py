"""Other parties: the party panel, public bodies (passive pole and other participants), and the attorney."""

import re

from bs4 import BeautifulSoup

from jurisync.connectors.pje import jsf
from jurisync.connectors.pje.filing.parsing import action_in_form, has_identifier, name_matches
from jurisync.connectors.pje.filing.payload import masked_cpf


class PartiesMixin:
    def party_panel(self, draft_url):
        html = self.get(draft_url)
        return self.ajax(
            self.update_url,
            jsf.view_state(html),
            {},
            "_viewRoot",
            {"tabPartes": "tabPartes", "ajaxSingle": "tabPartes"},
        )

    def _choose_party_role(self, html, pole):
        """Answer the "Tipo da Parte" step PJe 2.13 asks before the pre-registration form, when the portal has it.

        The select has generated ids, so it is found by its options; the pre-registration form must follow.
        """
        label = (getattr(self, "party_roles", None) or self.filing.party_roles or {}).get(pole)
        soup = BeautifulSoup(html, "html.parser")
        if not label:
            if soup.find("form", id="preCadastroPessoaFisicaForm") is None:
                offered = [
                    option.get_text(" ", strip=True)
                    for select in soup.find_all("select")
                    for option in select.find_all("option")[1:]
                ]
                if offered:
                    # The role depends on the class on 2nd degree (AGRAVANTE/AGRAVADO, IMPETRANTE...): it comes from the case.
                    raise RuntimeError(
                        f"PJe asks for the 'Tipo da Parte' of the {pole} pole (options: {', '.join(offered)}); "
                        f"set partyRoles.{pole} in the case facts"
                    )
            return html
        selects = [
            select
            for select in soup.find_all("select")
            if any(option.get_text(" ", strip=True) == label for option in select.find_all("option"))
        ]
        if len(selects) != 1 or selects[0].find_parent("form") is None:
            raise RuntimeError(f"PJe did not render the 'Tipo da Parte' step with the option {label}")
        select = selects[0]
        value = next(
            option["value"] for option in select.find_all("option") if option.get_text(" ", strip=True) == label
        )
        fields = jsf.form_fields(html, select.find_parent("form")["id"])
        fields[select["name"]] = value
        container, action = jsf.ajax_event(select)
        html = self.ajax(self.update_url, jsf.view_state(html), fields, container, action)
        if BeautifulSoup(html, "html.parser").find("form", id="preCadastroPessoaFisicaForm") is None:
            raise RuntimeError(f"PJe did not open the pre-registration form after choosing {label}")
        return html

    def _public_body(self, draft_url, body, *, role=None, panel=None):
        html = panel if panel is not None else self.party_panel(draft_url)
        panel_id = "poloPassivoDatalistDiv" if role is None else "outrosParticipantesDatalistDiv"
        panel = BeautifulSoup(html, "html.parser").find(id=panel_id)

        def present(panel):
            if panel is None:
                return False
            rows = [row.get_text(" ", strip=True) for row in panel.find_all("tr")]
            return any(has_identifier(text, body["cnpj"]) and (role is None or role in text) for text in rows)

        if present(panel):
            return {"alreadyAdded": True, "cnpjMatched": True}
        toolbar_id = "formVincularPoloPassivo" if role is None else "formVincularOutrosParticipantes"
        toolbar_action = "supResetarVincPartePP" if role is None else "supResetarVincParteOP"
        container, action = jsf.ajax_event(action_in_form(html, toolbar_id, toolbar_action))
        html = self.ajax(self.update_url, jsf.view_state(html), jsf.form_fields(html, toolbar_id), container, action)
        if role is None:
            html = self._choose_party_role(html, "passive")
        else:
            role_form = "formPasso1AssociarParticipante"
            soup = BeautifulSoup(html, "html.parser")
            selector, role_value, _ = jsf.select_option(soup, "comboParticipacao", role)
            fields = jsf.form_fields(html, role_form)
            fields[selector["name"]] = role_value
            container, action = jsf.ajax_event(selector)
            html = self.ajax(self.update_url, jsf.view_state(html), fields, container, action)
        lookup_form = "preCadastroPessoaFisicaForm" if role is None else "preCadastroParticipantePessoaFisicaForm"
        soup = BeautifulSoup(html, "html.parser")
        fields = jsf.form_fields(html, lookup_form)
        form = soup.find("form", id=lookup_form)
        person_type = [
            element
            for element in form.find_all("input")
            if (element.get("name") or "").endswith(":tipoPessoa") and element.get("value") == "J"
        ]
        public_body = [
            element
            for element in form.find_all("input")
            if (element.get("name") or "").endswith("isOrgaoPublico1SelectOneRadio") and element.get("value") == "true"
        ]
        if len(person_type) != 1 or len(public_body) != 1:
            raise RuntimeError("PJe did not render the public-body person options")
        name = jsf.field(soup, "_nomePJ")
        fields[person_type[0]["name"]] = "J"
        fields[public_body[0]["name"]] = "true"
        fields[name["name"]] = body["search"]
        search = jsf.field(soup, "btnPesquisarOrgaoPublico")
        container, action = jsf.ajax_event(search)
        html = self.ajax(self.update_url, jsf.view_state(html), fields, container, action)
        soup = BeautifulSoup(html, "html.parser")
        pages = 1
        slider = next(
            (script.get_text() for script in soup.find_all("script") if "new Richfaces.Slider" in script.get_text()), ""
        )
        if slider:
            slider = slider.replace("\\'", "'")
            slider_id = re.search(r'new Richfaces\.Slider\("([^"]+)"', slider)
            maximum = re.search(r"'maxValue':'(\d+)'", slider)
            if not slider_id or not maximum:
                raise RuntimeError("PJe public-body result pager could not be read")
            pages = int(maximum.group(1))
            if pages > 20:
                raise RuntimeError("PJe public-body lookup has too many result pages")
        else:
            slider_id = None
        row = None
        for page in range(1, pages + 1):
            candidates = [
                item for item in soup.find_all("tr") if has_identifier(item.get_text(" ", strip=True), body["cnpj"])
            ]
            if len(candidates) == 1:
                row = candidates[0]
                break
            if page == pages or slider_id is None:
                break
            fields[slider_id.group(1)] = str(page + 1)
            container, action = jsf.ajax_event({"onchange": slider})
            html = self.ajax(self.update_url, jsf.view_state(html), fields, container, action)
            soup = BeautifulSoup(html, "html.parser")
        if row is None:
            raise RuntimeError("PJe public-body lookup did not return the requested CNPJ")
        links = row.find_all("a", onclick=True)
        if len(links) != 1:
            raise RuntimeError("PJe public-body result has no unique selection action")
        container, action = jsf.ajax_event(links[0])
        html = self.ajax(self.update_url, jsf.view_state(html), fields, container, action)
        soup = BeautifulSoup(html, "html.parser")
        insert = jsf.field(soup, "btnInserirOrgPub")
        container, action = jsf.ajax_event(insert)
        html = self.ajax(self.update_url, jsf.view_state(html), fields, container, action)
        panel = BeautifulSoup(self.party_panel(draft_url), "html.parser").find(id=panel_id)
        if not present(panel):
            raise RuntimeError("PJe did not associate the public body with the draft")
        return {"alreadyAdded": False, "cnpjMatched": True}

    def add_passive_body(self, draft_url, body, panel=None):
        return self._public_body(draft_url, body, panel=panel)

    def add_other_participant(self, draft_url, body, panel=None):
        return self._public_body(draft_url, body, role=body["role"], panel=panel)

    def add_attorney(self, draft_url, attorney, panel=None):
        if (
            not all(attorney.get(key) for key in ("cpf", "name", "oabNumber", "oabState"))
            or attorney["oabState"] == jsf.NO_SELECTION
        ):
            raise ValueError("Attorney CPF, name, OAB number and state are required")

        def matches(panel):
            if panel is None:
                return False
            oab = re.compile(rf"\bOAB\s*{re.escape(attorney['oabState'])}\s*{re.escape(attorney['oabNumber'])}\b", re.I)
            return any(
                has_identifier(text, attorney["cpf"]) and name_matches(text, attorney["name"]) and oab.search(text)
                for text in (row.get_text(" ", strip=True) for row in panel.find_all("tr"))
            )

        html = panel if panel is not None else self.party_panel(draft_url)
        panel = BeautifulSoup(html, "html.parser").find(id="poloAtivoDatalistDiv")
        if matches(panel):
            return {"alreadyAdded": True, "identityMatched": True}
        if panel is not None and has_identifier(panel.get_text(" ", strip=True), attorney["cpf"]):
            raise RuntimeError("Draft attorney CPF is present with a different name or OAB")
        toolbar = "formVincularPoloAtivo"
        container, action = jsf.ajax_event(action_in_form(html, toolbar, "supResetarVincRepPA"))
        html = self.ajax(self.update_url, jsf.view_state(html), jsf.form_fields(html, toolbar), container, action)
        soup = BeautifulSoup(html, "html.parser")
        role, value, _ = jsf.select_option(soup, "comboRepresentacao", "ADVOGADO")
        fields = jsf.form_fields(html, "formTipoRepresentacao")
        fields[role["name"]] = value
        container, action = jsf.ajax_event(role)
        html = self.ajax(self.update_url, jsf.view_state(html), fields, container, action)
        soup = BeautifulSoup(html, "html.parser")
        fields = jsf.form_fields(html, "preCadastroAdvogadoForm")
        cpf = jsf.field(soup, "preCadastroAdvogado_nrCPF")
        fields[cpf["name"]] = masked_cpf(attorney["cpf"])
        search = jsf.field(soup, "btnPesquisarAdvogado")
        container, action = jsf.ajax_event(search)
        html = self.ajax(self.update_url, jsf.view_state(html), fields, container, action)
        soup = BeautifulSoup(html, "html.parser")
        name = jsf.field(soup, "dsNomeAdvogadoCivil")
        if not name_matches(name.get("value", ""), attorney["name"]):
            raise RuntimeError("PJe attorney lookup did not match the expected name")
        fields[name["name"]] = name["value"]
        confirm = jsf.field(soup, "btnConfirmarCadastro")
        container, action = jsf.ajax_event(confirm)
        html = self.ajax(self.update_url, jsf.view_state(html), fields, container, action)
        soup = BeautifulSoup(html, "html.parser")
        fields = jsf.form_fields(html, "formInserirRepresentanteProcesso")
        insert = jsf.field(soup, "btnInserirRepresentante")
        container, action = jsf.ajax_event(insert)
        self.ajax(self.update_url, jsf.view_state(html), fields, container, action)
        panel = BeautifulSoup(self.party_panel(draft_url), "html.parser").find(id="poloAtivoDatalistDiv")
        if not matches(panel):
            raise RuntimeError("PJe did not associate the attorney with the author")
        return {"alreadyAdded": False, "identityMatched": True}
