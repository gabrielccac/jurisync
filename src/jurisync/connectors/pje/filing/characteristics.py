"""Draft characteristics: legal aid, injunction, claim value, and priority."""

from bs4 import BeautifulSoup

from jurisync.connectors.pje import jsf
from jurisync.connectors.pje.filing.constants import UPDATE_URL
from jurisync.connectors.pje.filing.payload import claim_amount, validate_characteristics


class CharacteristicsMixin:
    def characteristics_panel(self, draft_url):
        html = self.get(draft_url)
        return self.ajax(
            UPDATE_URL,
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
        current = jsf.form_fields(html, "formAdicionarCaracteristicasProcesso")
        already_saved = (
            current.get(legal_aid["name"]) == fields[legal_aid["name"]]
            and current.get(injunction["name"]) == fields[injunction["name"]]
            and claim_amount(current.get(claim["name"], "")) == claim_value
        )
        if not already_saved:
            save = jsf.field(soup, "salvaCaracteristicaProcessoButton")
            container, action = jsf.ajax_event(save)
            self.ajax(UPDATE_URL, jsf.view_state(html), fields, container, action)
            html = self.characteristics_panel(draft_url)
            soup = BeautifulSoup(html, "html.parser")
        saved = jsf.form_fields(html, "formAdicionarCaracteristicasProcesso")
        expected = {
            legal_aid["name"]: str(characteristics["legalAid"]).lower(),
            injunction["name"]: str(characteristics["injunction"]).lower(),
            claim["name"]: claim_value,
        }
        for name, value in expected.items():
            actual = claim_amount(saved.get(name, "")) if name == claim["name"] else saved.get(name)
            if actual != value:
                raise RuntimeError(f"PJe did not retain the characteristic {name.rsplit(':', 1)[-1]}")
        priority_label = characteristics.get("priority")
        priority_added = False
        if priority_label:
            list_node = soup.find(id="processoPrioridadeProcessoAbaList")
            if list_node is None or priority_label not in list_node.get_text(" ", strip=True):
                selector, value, _ = jsf.select_option(
                    soup, "prioridadeProcessoDecoration:prioridadeProcesso", priority_label
                )
                priority_fields = jsf.form_fields(html, "formAddPrioridadeProcesso")
                priority_fields[selector["name"]] = value
                save_priority = jsf.field(soup, "formAddPrioridadeProcesso:save")
                container, action = jsf.ajax_event(selector)
                html = self.ajax(UPDATE_URL, jsf.view_state(html), priority_fields, container, action)
                container, action = jsf.ajax_event(save_priority)
                html = self.ajax(UPDATE_URL, jsf.view_state(html), priority_fields, container, action)
                priority_added = True
                html = self.characteristics_panel(draft_url)
                if priority_label not in BeautifulSoup(html, "html.parser").get_text(" ", strip=True):
                    raise RuntimeError("PJe did not retain the requested priority")
        return {
            "claimValue": claim_value,
            "legalAid": characteristics["legalAid"],
            "injunction": characteristics["injunction"],
            "priority": priority_label,
            "priorityAdded": priority_added,
            "alreadySaved": already_saved,
        }
