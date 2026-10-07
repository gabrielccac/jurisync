"""The author party and the address selected for it."""

import logging
import re

from bs4 import BeautifulSoup

from jurisync.connectors.pje import jsf
from jurisync.connectors.pje.filing.constants import UPDATE_URL
from jurisync.connectors.pje.filing.parsing import has_identifier, name_matches
from jurisync.connectors.pje.filing.payload import address_cep, masked_cpf
from jurisync.connectors.pje.parsers import jsf_data

log = logging.getLogger("jurisync.pje.filing")


class AuthorMixin:
    def add_author(self, draft_url, author_cpf, expected_name, address=None, panel=None, inline_address=True):
        cpf_mask = masked_cpf(author_cpf)
        html = panel if panel is not None else self.party_panel(draft_url)
        soup = BeautifulSoup(html, "html.parser")
        active = soup.find(id="poloAtivoDatalistDiv")
        if active is not None and name_matches(active.get_text(" ", strip=True), expected_name):
            result = {"alreadyAdded": True, "authorNameMatched": True}
            if address is not None:
                result["address"] = self.ensure_author_address(draft_url, author_cpf, expected_name, address)
            return result
        toolbar = soup.find("form", id="formVincularPoloAtivo")
        if toolbar is None:
            raise RuntimeError("PJe did not render the active-party toolbar")
        add_button = toolbar.find(id="addParteA")
        if add_button is None or add_button.find_parent(attrs={"onclick": True}) is None:
            raise RuntimeError("PJe active-party action was not rendered")
        container, action = jsf.ajax_event(add_button.find_parent(attrs={"onclick": True}))
        html = self.ajax(
            UPDATE_URL, jsf.view_state(html), jsf.form_fields(html, "formVincularPoloAtivo"), container, action
        )
        soup = BeautifulSoup(html, "html.parser")
        person = jsf.field(soup, "tipoPessoa:0")
        fields = jsf.form_fields(html, "preCadastroPessoaFisicaForm")
        fields[person["name"]] = "F"
        container, action = jsf.ajax_event(person)
        html = self.ajax(UPDATE_URL, jsf.view_state(html), fields, container, action)
        soup = BeautifulSoup(html, "html.parser")
        fields = {key: value for key, value in fields.items() if "isOrgaoPublico1" not in key}
        fields.update(
            {key: value for key, value in jsf_data(html)[1].items() if key.startswith("preCadastroPessoaFisicaForm:")}
        )
        brazilian = jsf.field(soup, "isBrasileiroSelectOneRadio:0")
        cpf = jsf.field(soup, "preCadastroPessoaFisica_nrCPF")
        fields[brazilian["name"]] = "true"
        fields[cpf["name"]] = cpf_mask
        search = jsf.field(soup, "pesquisarDocumentoPrincipal")
        container, action = jsf.ajax_event(search)
        html = self.ajax(UPDATE_URL, jsf.view_state(html), fields, container, action)
        soup = BeautifulSoup(html, "html.parser")
        registry_name = jsf.field(soup, "dsNomeCivil").get("value", "")
        if not name_matches(registry_name, expected_name):
            raise RuntimeError("Author CPF lookup did not match the expected name")
        fields = jsf.form_fields(html, "preCadastroPessoaFisicaForm")
        confirm = jsf.field(soup, "btnConfirmarCadastro")
        container, action = jsf.ajax_event(confirm)
        html = self.ajax(UPDATE_URL, jsf.view_state(html), fields, container, action)
        soup = BeautifulSoup(html, "html.parser")
        details = soup.find("form", id="formInserirParteProcesso")
        if details is None:
            raise RuntimeError("PJe did not render author details")
        detail_name = jsf.field(details, "nomeDecoration:nome").get("value", "")
        if not name_matches(detail_name, expected_name):
            raise RuntimeError("Author details did not match the CPF lookup")
        for suffix in ("nomeGenitoraDecoration:nomeGenitora", "dataNascimentoDecoration:dataNascimentoInputDate"):
            if not jsf.field(details, suffix).get("value"):
                raise RuntimeError(f"PJe returned no author detail: {suffix}")
        insert = jsf.field(details, "btnInserirParteProcesso")
        detail_fields = jsf.form_fields(html, "formInserirParteProcesso")
        address_tab = jsf.field(soup, "enderecoUsuario_shifted", tag="table")
        container, action = jsf.ajax_event(address_tab)
        html = self.ajax(UPDATE_URL, jsf.view_state(html), detail_fields, container, action)
        inline = None
        if address is not None and inline_address:
            try:
                html, inline = self._enter_address_before_insert(html, detail_fields, address)
            except Exception as error:
                # Nothing is saved until Incluir, so a reload discards the form and the classic flow can start over.
                log.warning("Inline author address failed (%s); using the separate address step", error)
                return self.add_author(draft_url, author_cpf, expected_name, address, inline_address=False)
        address_fields = inline if inline is not None else self._author_form_fields(html, detail_fields)
        container, action = jsf.ajax_event(insert)
        html = self.ajax(UPDATE_URL, jsf.view_state(html), address_fields, container, action)
        soup = BeautifulSoup(html, "html.parser")
        active = soup.find(id="poloAtivoDatalistDiv")
        if active is None:
            html = self.get(draft_url)
            html = self.ajax(
                UPDATE_URL, jsf.view_state(html), {}, "_viewRoot", {"tabPartes": "tabPartes", "ajaxSingle": "tabPartes"}
            )
            active = BeautifulSoup(html, "html.parser").find(id="poloAtivoDatalistDiv")
        if active is None or not name_matches(active.get_text(" ", strip=True), expected_name):
            raise RuntimeError("PJe did not render the author in the active-party list")
        result = {"alreadyAdded": False, "authorNameMatched": True}
        if address is not None:
            result["address"] = self.ensure_author_address(draft_url, author_cpf, expected_name, address, html)
        return result

    @staticmethod
    def _author_form_fields(html, detail_fields, strict=True):
        """Inputs PJe expects with Incluir: the address block plus four fields from the details form."""
        fields = {
            key: value
            for key, value in jsf_data(html)[1].items()
            if key.startswith("formInserirParteProcesso:")
            and "enderecoGridTabList" not in key
            and ("cadastroPartePessoaEndereco" in key or key.endswith("_selection"))
            and "btn" not in key
        }
        if strict and len(fields) != 8:
            raise RuntimeError(f"PJe returned {len(fields)} address inputs instead of 8")
        for suffix in ("tpParteAlteracao", "comboParteSigilosa", "comboMoradorRua", "comboRepresentante"):
            matches = [key for key in detail_fields if key.endswith(suffix)]
            if len(matches) != 1:
                raise RuntimeError(f"PJe did not render one author field: {suffix}")
            fields[matches[0]] = detail_fields[matches[0]]
        fields.update({"formInserirParteProcesso": "formInserirParteProcesso", "autoScroll": ""})
        return fields

    def _enter_address_before_insert(self, html, detail_fields, address):
        """Enter and select the address in the insert form, so the separate editor session is not needed."""
        cep = address_cep(address)
        # The address-tab response is a partial page: start from the full form and add the address inputs.
        fields = dict(detail_fields)
        fields.update(
            {
                key: value
                for key, value in jsf_data(html)[1].items()
                if key.startswith("formInserirParteProcesso:") and "enderecoGridTabList" not in key and "btn" not in key
            }
        )
        try:
            rows = self._address_rows(html, cep)
        except RuntimeError:
            rows = []
        if not rows:
            html, rows = self._register_address(html, fields, address, cep)
        if len(rows) != 1:
            raise RuntimeError(f"Expected one address with CEP {cep}, got {len(rows)}")
        link, selected, number = rows[0]
        if number != address["numero"].strip():
            raise RuntimeError("PJe did not show the requested author address number")
        if not selected:
            form = link.find_parent("form")
            container, action = jsf.ajax_event(link)
            html = self.ajax(UPDATE_URL, jsf.view_state(html), jsf.form_fields(html, form["id"]), container, action)
            rows = self._address_rows(html, cep)
            if len(rows) != 1 or not rows[0][1]:
                raise RuntimeError("PJe did not select the requested author address")
        return html, self._author_form_fields(html, detail_fields, strict=False)

    def _author_address_editor(self, draft_url, author_cpf, expected_name, party_html=None):
        html = party_html if party_html is not None else self.party_panel(draft_url)
        active = BeautifulSoup(html, "html.parser").find(id="poloAtivoDatalistDiv")
        if active is None:
            raise RuntimeError("PJe did not render the active-party list")
        matches = []
        for link in active.find_all("a", id=re.compile(r"gridPartesPoloAtivoEdit$")):
            row = link.find_parent("tr", class_="rich-table-row")
            if (
                row
                and name_matches(row.get_text(" ", strip=True), expected_name)
                and has_identifier(row.get_text(" ", strip=True), author_cpf)
            ):
                matches.append(link)
        if len(matches) != 1:
            raise RuntimeError(f"Expected one editable author matching name and CPF, got {len(matches)}")
        link = matches[0]
        form = link.find_parent("form")
        container, action = jsf.ajax_event(link)
        html = self.ajax(UPDATE_URL, jsf.view_state(html), jsf.form_fields(html, form["id"]), container, action)
        soup = BeautifulSoup(html, "html.parser")
        details = soup.find("form", id="formInserirParteProcesso")
        if details is None or not name_matches(
            jsf.field(details, "nomeDecoration:nome").get("value", ""), expected_name
        ):
            raise RuntimeError("PJe author editor did not match the expected name")
        fields = jsf.form_fields(html, "formInserirParteProcesso")
        update = jsf.field(soup, "btnAtualizarParte")
        update_action = jsf.ajax_event(update)
        tab = jsf.field(soup, "enderecoUsuario_shifted", tag="table")
        container, action = jsf.ajax_event(tab)
        html = self.ajax(UPDATE_URL, jsf.view_state(html), fields, container, action)
        address_fields = {
            key: value
            for key, value in jsf_data(html)[1].items()
            if key.startswith("formInserirParteProcesso:")
            and "enderecoGridTabList" not in key
            and "cadastroPartePessoaEndereco" in key
            and "btn" not in key
        }
        for suffix in ("tpParteAlteracao", "comboParteSigilosa", "comboMoradorRua", "comboRepresentante"):
            matches = [key for key in fields if key.endswith(suffix)]
            if len(matches) != 1:
                raise RuntimeError(f"PJe did not render one author field: {suffix}")
            address_fields[matches[0]] = fields[matches[0]]
        address_fields.update({"formInserirParteProcesso": "formInserirParteProcesso", "autoScroll": ""})
        return html, address_fields, update_action

    @staticmethod
    def _address_rows(html, cep):
        table = BeautifulSoup(html, "html.parser").find(
            "table", id=re.compile(r"cadastroPartePessoaEnderecoenderecoGridTabList$")
        )
        if table is None:
            raise RuntimeError("PJe did not render the author's address list")
        rows = []
        for row in table.find_all("tr", class_="rich-table-row"):
            cells = row.find_all("td", recursive=False)
            if len(cells) < 6 or cells[2].get_text(" ", strip=True) != cep:
                continue
            links = [
                link
                for link in row.find_all("a", onclick=True)
                if link.find("img", src=re.compile(r"/radio-button(?:-uncheck)?_16x16\.png$"))
            ]
            if len(links) != 1:
                raise RuntimeError("PJe did not render the address selection action")
            selected = "uncheck" not in links[0].find("img")["src"]
            rows.append((links[0], selected, cells[5].get_text(" ", strip=True)))
        return rows

    def _register_address(self, html, fields, address, cep):
        """Enter a new address in the open party form (CEP suggestion, number, Incluir); returns (html, rows)."""
        if not isinstance(address.get("numero"), str) or not address["numero"].strip():
            raise ValueError("author.address.numero is required to add a new address")
        soup = BeautifulSoup(html, "html.parser")
        cep_input = jsf.field(soup, "cadastroPartePessoaEnderecoCEP")
        script = next(
            (
                script.get_text()
                for script in soup.find_all("script")
                if "new RichFaces.Suggestion" in script.get_text() and cep_input["id"] in script.get_text()
            ),
            "",
        )
        suggestion = re.search(
            r"new RichFaces\.Suggestion\('formInserirParteProcesso','" + re.escape(cep_input["id"]) + r"','([^']+)'",
            script,
        )
        if suggestion:
            script = script[suggestion.start() :]
        container = re.search(r"'containerId':'([^']+)'", script)
        selection = re.search(r"'onselect':function.*?'similarityGroupingId':'([^']+)'", script, re.S)
        if not (suggestion and container and selection):
            raise RuntimeError("PJe did not render the CEP suggestion action")
        suggestion_id = suggestion.group(1)
        fields[cep_input["name"]] = cep
        html = self.ajax(
            UPDATE_URL,
            jsf.view_state(html),
            fields,
            container.group(1),
            {suggestion_id: suggestion_id, "ajaxSingle": suggestion_id, "inputvalue": cep},
        )
        entries = BeautifulSoup(html, "html.parser").find_all("tr", class_="richfaces_suggestionEntry")
        if len(entries) != 1:
            raise RuntimeError(f"PJe CEP lookup returned {len(entries)} selectable addresses; manual review required")
        fields[f"{suggestion_id}_selection"] = "0"
        html = self.ajax(
            UPDATE_URL,
            jsf.view_state(html),
            fields,
            container.group(1),
            {selection.group(1): selection.group(1), "ajaxSingle": suggestion_id},
        )
        soup = BeautifulSoup(html, "html.parser")
        if re.sub(r"\D", "", jsf.field(soup, "cadastroPartePessoaEnderecoCEP").get("value", "")) != re.sub(
            r"\D", "", cep
        ):
            raise RuntimeError("PJe selected a different CEP")
        for key, value in jsf_data(html)[1].items():
            if key.startswith("formInserirParteProcesso:") and "enderecoGridTabList" not in key and "btn" not in key:
                fields[key] = value
        for suffix, value in (
            ("cadastroPartePessoaEndereconumeroEndereco", address["numero"].strip()),
            ("cadastroPartePessoaEnderecocomplemento", address.get("complemento") or ""),
        ):
            fields[jsf.field(soup, suffix)["name"]] = value
        save = jsf.field(soup, "cadastroPartePessoaEnderecobtnGravarEndereco")
        container_id, action = jsf.ajax_event(save)
        html = self.ajax(UPDATE_URL, jsf.view_state(html), fields, container_id, action)
        rows = self._address_rows(html, cep)
        fields.update({key: value for key, value in jsf_data(html)[1].items() if key in fields})
        return html, rows

    def ensure_author_address(self, draft_url, author_cpf, expected_name, address, party_html=None):
        cep = address_cep(address)
        try:
            html, fields, update_action = self._author_address_editor(draft_url, author_cpf, expected_name, party_html)
        except RuntimeError:
            if party_html is None:
                raise
            html, fields, update_action = self._author_address_editor(draft_url, author_cpf, expected_name)
        rows = self._address_rows(html, cep)
        added = False
        if not rows:
            html, rows = self._register_address(html, fields, address, cep)
            added = True
        if len(rows) != 1:
            raise RuntimeError(f"Expected one address with CEP {cep}, got {len(rows)}")
        link, selected, number = rows[0]
        if added and number != address["numero"].strip():
            raise RuntimeError("PJe did not show the requested author address number after Incluir")
        if not selected:
            form = link.find_parent("form")
            container, action = jsf.ajax_event(link)
            html = self.ajax(UPDATE_URL, jsf.view_state(html), jsf.form_fields(html, form["id"]), container, action)
            rows = self._address_rows(html, cep)
            if len(rows) != 1 or not rows[0][1]:
                raise RuntimeError("PJe did not select the requested author address")
        if not added and selected:
            return {"cep": cep, "numero": number, "added": False, "verified": True, "changed": False}
        container, action = update_action
        self.ajax(UPDATE_URL, jsf.view_state(html), fields, container, action)
        saved_html, _, _ = self._author_address_editor(draft_url, author_cpf, expected_name)
        saved_rows = self._address_rows(saved_html, cep)
        if len(saved_rows) != 1 or not saved_rows[0][1]:
            raise RuntimeError("PJe did not retain the selected author address after reopening the party")
        if added and saved_rows[0][2] != address["numero"].strip():
            raise RuntimeError("PJe did not retain the requested author address number")
        return {"cep": cep, "numero": saved_rows[0][2], "added": added, "verified": True, "changed": True}
