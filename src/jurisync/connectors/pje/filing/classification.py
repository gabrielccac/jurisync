"""Draft creation: the classification form (Dados iniciais) and the Incluir action."""

from urllib.parse import parse_qs, urljoin, urlsplit

from bs4 import BeautifulSoup

from jurisync.connectors.pje import jsf


class ClassificationMixin:
    def _classification_form(self, classification):
        html = self.get(self.register_url)
        fields = jsf.form_fields(html, "processoTrfForm")
        state = jsf.view_state(html)
        soup = BeautifulSoup(html, "html.parser")
        include = jsf.field(soup, "incluiProcessoButton")["name"]
        area, area_value, area_count = jsf.select_option(soup, "areaDireitoCombo", classification["area"])
        fields[area["name"]] = area_value
        container, action = jsf.ajax_event(area)
        html = self.post(
            self.register_url,
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
            self.register_url,
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
            self.register_url,
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

    @staticmethod
    def _labels(soup, suffix):
        selects = [select for select in soup.find_all("select") if (select.get("name") or "").endswith(suffix)]
        if len(selects) != 1:
            return None
        return [
            option.get_text(" ", strip=True)
            for option in selects[0].find_all("option")
            if option.get("value") not in (None, "", jsf.NO_SELECTION)
        ]

    def register_options(self, area=None, jurisdiction=None):
        """Read the new-process form's choices without creating anything: the areas, the jurisdictions of `area`, and
        the judicial classes of `jurisdiction`. Only the form's own Ajax refreshes are sent, never Incluir."""
        html = self.get(self.register_url)
        fields = jsf.form_fields(html, "processoTrfForm")
        soup = BeautifulSoup(html, "html.parser")
        result = {"portal": self.portal.key, "areas": self._labels(soup, "areaDireitoCombo")}
        for label, suffix, key in (
            (area, "areaDireitoCombo", "jurisdictions"),
            (jurisdiction, "jurisdicaoCombo", "classes"),
        ):
            if not label:
                break
            select, value, _ = jsf.select_option(soup, suffix, label)
            fields[select["name"]] = value
            container, action = jsf.ajax_event(select)
            state = jsf.view_state(html)
            html = self.post(
                self.register_url,
                {
                    **fields,
                    "AJAXREQUEST": container,
                    "javax.faces.ViewState": state,
                    "AJAX:EVENTS_COUNT": "1",
                    **action,
                },
            )
            soup = BeautifulSoup(html, "html.parser")
            result[key] = self._labels(soup, "jurisdicaoCombo" if key == "jurisdictions" else "classeJudicialCombo")
        return result

    def classification_options(self, classification):
        return self._classification_form(classification)[3]

    def create_draft(self, classification):
        html, fields, include, options = self._classification_form(classification)
        fields[include] = include
        html = self.ajax(self.register_url, jsf.view_state(html), fields, "_viewRoot", {})
        soup = BeautifulSoup(html, "html.parser")
        redirect = soup.find("meta", attrs={"name": "Location"})
        location = redirect.get("content", "") if redirect else ""
        draft_url = urljoin(self.register_url, location)
        parsed = urlsplit(draft_url)
        process_ids = parse_qs(parsed.query).get("idProcesso", [])
        if (
            parsed.hostname != self.portal.host
            or parsed.path != urlsplit(self.update_url).path
            or len(process_ids) != 1
        ):
            # Say what PJe said (a required field it asked for, a validation message), and that a draft may exist.
            messages = [
                " ".join(node.get_text(" ", strip=True).split())
                for node in soup.select(".rich-messages-label, [id$='processoTrfFormMessage']")
                if node.get_text(strip=True)
            ]
            detail = f": {'; '.join(dict.fromkeys(messages))}" if messages else ""
            raise RuntimeError(
                f"PJe did not return a new draft URL after Incluir{detail}. Check Não Protocolados before trying again"
            )
        opened = self.get(draft_url)
        jsf.view_state(opened)
        return {"draftId": process_ids[0], "draftUrl": draft_url, "classification": options}
