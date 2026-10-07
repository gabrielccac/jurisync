"""Draft creation: the classification form (Dados iniciais) and the Incluir action."""

from urllib.parse import parse_qs, urljoin, urlsplit

from bs4 import BeautifulSoup

from jurisync.connectors.pje import jsf
from jurisync.connectors.pje.filing.constants import PORTAL, REGISTER_URL, UPDATE_URL


class ClassificationMixin:
    def _classification_form(self, classification):
        html = self.get(REGISTER_URL)
        fields = jsf.form_fields(html, "processoTrfForm")
        state = jsf.view_state(html)
        soup = BeautifulSoup(html, "html.parser")
        include = jsf.field(soup, "incluiProcessoButton")["name"]
        area, area_value, area_count = jsf.select_option(soup, "areaDireitoCombo", classification["area"])
        fields[area["name"]] = area_value
        container, action = jsf.ajax_event(area)
        html = self.post(
            REGISTER_URL,
            {**fields, "AJAXREQUEST": container, "javax.faces.ViewState": state, "AJAX:EVENTS_COUNT": "1", **action},
        )
        state = jsf.view_state(html)
        soup = BeautifulSoup(html, "html.parser")
        jurisdiction, jurisdiction_value, jurisdiction_count = jsf.select_option(
            soup, "jurisdicaoCombo", classification["jurisdiction"]
        )
        fields[jurisdiction["name"]] = jurisdiction_value
        container, action = jsf.ajax_event(jurisdiction)
        html = self.post(
            REGISTER_URL,
            {**fields, "AJAXREQUEST": container, "javax.faces.ViewState": state, "AJAX:EVENTS_COUNT": "1", **action},
        )
        state = jsf.view_state(html)
        soup = BeautifulSoup(html, "html.parser")
        judicial_class, class_value, class_count = jsf.select_option(
            soup, "classeJudicialCombo", classification["judicialClass"]
        )
        fields[judicial_class["name"]] = class_value
        container, action = jsf.ajax_event(judicial_class)
        html = self.post(
            REGISTER_URL,
            {**fields, "AJAXREQUEST": container, "javax.faces.ViewState": state, "AJAX:EVENTS_COUNT": "1", **action},
        )
        jsf.view_state(html)
        options = {
            "area": classification["area"],
            "areaOptions": area_count,
            "jurisdiction": classification["jurisdiction"],
            "jurisdictionOptions": jurisdiction_count,
            "judicialClass": classification["judicialClass"],
            "classOptions": class_count,
        }
        return html, fields, include, options

    def classification_options(self, classification):
        return self._classification_form(classification)[3]

    def create_draft(self, classification):
        html, fields, include, options = self._classification_form(classification)
        fields[include] = include
        html = self.ajax(REGISTER_URL, jsf.view_state(html), fields, "_viewRoot", {})
        soup = BeautifulSoup(html, "html.parser")
        redirect = soup.find("meta", attrs={"name": "Location"})
        location = redirect.get("content", "") if redirect else ""
        draft_url = urljoin(REGISTER_URL, location)
        parsed = urlsplit(draft_url)
        process_ids = parse_qs(parsed.query).get("idProcesso", [])
        if parsed.hostname != PORTAL.host or parsed.path != urlsplit(UPDATE_URL).path or len(process_ids) != 1:
            raise RuntimeError("PJe did not return a new draft URL after Incluir")
        opened = self.get(draft_url)
        jsf.view_state(opened)
        return {"draftId": process_ids[0], "draftUrl": draft_url, "classification": options}
