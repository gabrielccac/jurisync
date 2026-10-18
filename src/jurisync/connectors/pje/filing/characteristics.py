"""Draft characteristics: legal aid, injunction, claim value, and priority."""

from bs4 import BeautifulSoup

from jurisync.connectors.pje import jsf
from jurisync.connectors.pje.filing.payload import claim_amount, priority_labels, validate_characteristics


class CharacteristicsMixin:
    def characteristics_panel(self, draft_url):
        html = self.get(draft_url)
        return self.ajax(
            self.update_url,
            jsf.view_state(html),
            {},
            "_viewRoot",
            {"processoPrioridadeProcesso": "processoPrioridadeProcesso", "ajaxSingle": "processoPrioridadeProcesso"},
        )

    def save_characteristics(self, draft_url, characteristics):
        claim_value = validate_characteristics(characteristics)
        html = self.characteristics_panel(draft_url)
        soup = BeautifulSoup(html, "html.parser")
        fields = jsf.form_fields(html, "formAdicionarCaracteristicasProcesso")
        legal_aid = jsf.field(soup, "justicaGratuitaSelectOneRadio:0")
        injunction = jsf.field(soup, "tutelaLiminarSelectOneRadio:0")
        claim = jsf.field(soup, "valorCausaDecoration:valorCausa")
        fields[legal_aid["name"]] = str(characteristics["legalAid"]).lower()
        fields[injunction["name"]] = str(characteristics["injunction"]).lower()
        fields[claim["name"]] = claim_value
        radios = [legal_aid, injunction]
        if "digitalCourt" in characteristics:
            # TRF1 (PJe 2.13) asks "Deseja solicitar juízo 100% digital?" in this form; TRF5 (2.11) has it as a priority.
            digital = soup.find(
                "input",
                attrs={"name": lambda name: (name or "").endswith("solicitadoJuizo100PorCentoDigitalSelectOneRadio")},
            )
            if digital is None:
                raise RuntimeError(
                    "This PJe has no Juízo 100% Digital field; where it is a priority (TRF5), list it in priority instead"
                )
            fields[digital["name"]] = str(characteristics["digitalCourt"]).lower()
            radios.append(digital)
        current = jsf.form_fields(html, "formAdicionarCaracteristicasProcesso")
        already_saved = all(current.get(radio["name"]) == fields[radio["name"]] for radio in radios) and (
            claim_amount(current.get(claim["name"], "")) == claim_value
        )
        if not already_saved:
            save = jsf.field(soup, "salvaCaracteristicaProcessoButton")
            container, action = jsf.ajax_event(save)
            self.ajax(self.update_url, jsf.view_state(html), fields, container, action)
            html = self.characteristics_panel(draft_url)
            soup = BeautifulSoup(html, "html.parser")
        saved = jsf.form_fields(html, "formAdicionarCaracteristicasProcesso")
        expected = {radio["name"]: fields[radio["name"]] for radio in radios}
        expected[claim["name"]] = claim_value
        for name, value in expected.items():
            actual = claim_amount(saved.get(name, "")) if name == claim["name"] else saved.get(name)
            if actual != value:
                raise RuntimeError(f"PJe did not retain the characteristic {name.rsplit(':', 1)[-1]}")
        priorities, added = [], []
        for label in priority_labels(characteristics):
            html, was_added, shown = self._ensure_priority(draft_url, html, label)
            priorities.append(shown)
            if was_added:
                added.append(shown)
        secrecy = None
        if "secrecy" in characteristics:
            html, secrecy = self._ensure_secrecy(draft_url, html, characteristics["secrecy"])
        return {
            "claimValue": claim_value,
            "legalAid": characteristics["legalAid"],
            "injunction": characteristics["injunction"],
            **({"digitalCourt": characteristics["digitalCourt"]} if "digitalCourt" in characteristics else {}),
            "priority": priorities,
            "priorityAdded": added,
            **({"secrecy": secrecy} if secrecy is not None else {}),
            "alreadySaved": already_saved,
        }

    @staticmethod
    def _attached_priorities(html):
        node = BeautifulSoup(html, "html.parser").find(id="processoPrioridadeProcessoAbaList")
        return " ".join(node.get_text(" ", strip=True).split()).casefold() if node else ""

    def _ensure_priority(self, draft_url, html, label):
        """Add one priority unless the draft lists it; PJe's labels vary in case (TRF1: 'Pessoa com deficiência')."""
        if label.casefold() in self._attached_priorities(html):
            return html, False, label
        soup = BeautifulSoup(html, "html.parser")
        selects = [
            s
            for s in soup.find_all("select")
            if (s.get("name") or "").endswith("prioridadeProcessoDecoration:prioridadeProcesso")
        ]
        options = [
            option
            for select in selects
            for option in select.find_all("option")
            if option.get_text(" ", strip=True).casefold() == label.casefold()
        ]
        if len(selects) != 1 or len(options) != 1:
            offered = [o.get_text(" ", strip=True) for s in selects for o in s.find_all("option")][1:]
            raise RuntimeError(f"PJe offers no priority {label!r}; options: {', '.join(offered)}")
        shown = options[0].get_text(" ", strip=True)
        selector, value, _ = jsf.select_option(soup, "prioridadeProcessoDecoration:prioridadeProcesso", shown)
        priority_fields = jsf.form_fields(html, "formAddPrioridadeProcesso")
        priority_fields[selector["name"]] = value
        save_priority = jsf.field(soup, "formAddPrioridadeProcesso:save")
        container, action = jsf.ajax_event(selector)
        html = self.ajax(self.update_url, jsf.view_state(html), priority_fields, container, action)
        container, action = jsf.ajax_event(save_priority)
        self.ajax(self.update_url, jsf.view_state(html), priority_fields, container, action)
        html = self.characteristics_panel(draft_url)
        if shown.casefold() not in self._attached_priorities(html):
            raise RuntimeError(f"PJe did not retain the priority {shown!r}")
        return html, True, shown

    def _ensure_secrecy(self, draft_url, html, secrecy):
        """Segredo de justiça has its own form (frmSegredoSig) and save; only "Não" is set here, and read back."""
        if secrecy is not False:
            raise ValueError("Only segredo de justiça = Não is set automatically")
        soup = BeautifulSoup(html, "html.parser")
        form = soup.find("form", id="frmSegredoSig")
        if form is None:
            raise RuntimeError("PJe did not render the segredo de justiça form")
        radios = {r.get("value"): r for r in form.find_all("input", attrs={"type": "radio"})}
        if "false" not in radios:
            raise RuntimeError("PJe did not render the segredo de justiça 'Não' option")
        if radios["false"].has_attr("checked"):
            return html, {"value": False, "changed": False}
        fields = jsf.form_fields(html, "frmSegredoSig")
        fields[radios["false"]["name"]] = "false"
        container, action = jsf.ajax_event(jsf.field(soup, "frmSegredoSig:grvSegredo"))
        self.ajax(self.update_url, jsf.view_state(html), fields, container, action)
        html = self.characteristics_panel(draft_url)
        saved = BeautifulSoup(html, "html.parser").find("form", id="frmSegredoSig")
        no = saved.find("input", attrs={"type": "radio", "value": "false"}) if saved else None
        if no is None or not no.has_attr("checked"):
            raise RuntimeError("PJe did not retain segredo de justiça = Não")
        return html, {"value": False, "changed": True}
