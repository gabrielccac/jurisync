"""Não Protocolados: find drafts and confirm one is listed."""

from urllib.parse import parse_qs, urljoin, urlsplit

from bs4 import BeautifulSoup

from jurisync.connectors.pje import jsf
from jurisync.connectors.pje.filing.constants import PENDING_URL
from jurisync.connectors.pje.filing.payload import masked_cpf


class PendingMixin:
    def search_pending_drafts(self, *, name=None, cpf=None):
        name = (name or "").strip()
        if not (name or cpf):
            raise ValueError("Name or CPF is required to search Não Protocolados")
        html = self.get(PENDING_URL)
        soup = BeautifulSoup(html, "html.parser")
        fields = jsf.form_fields(html, "consultaProcessoNaoProtocoladoForm")
        if name:
            fields[jsf.field(soup, "nomeParteDecoration:nomeParte")["name"]] = name
        if cpf:
            fields[jsf.field(soup, "numeroCPFCNPJRadioCPFCNPJ:numeroCPFCNPJCPF")["name"]] = masked_cpf(cpf)
            radios = [
                radio
                for radio in soup.find_all("input", attrs={"type": "radio"})
                if "numeroCPFCNPJRadioCPFCNPJ" in (radio.get("name") or "") and radio.get("value") == "false"
            ]
            if len(radios) != 1:
                raise RuntimeError("PJe did not render the CPF selection")
            fields[radios[0]["name"]] = "false"
        search = jsf.field(soup, "consultaProcessoNaoProtocoladoForm:consulta")
        container, action = jsf.ajax_event(search)
        html = self.ajax(PENDING_URL, jsf.view_state(html), fields, container, action)
        table = BeautifulSoup(html, "html.parser").find(id="consultaProcessoNaoProtocoladoGridList")
        if table is None:
            raise RuntimeError("PJe did not render the unfiled-process results")
        results = []
        for row in table.find_all("tr"):
            links = [a for a in row.find_all("a", href=True) if a["href"].startswith("/pje/Processo/update.seam?")]
            if not links:
                continue
            if len(links) != 1:
                raise RuntimeError("PJe returned multiple opening links for one draft")
            url = urljoin(PENDING_URL, links[0]["href"])
            draft_ids = parse_qs(urlsplit(url).query).get("idProcesso", [])
            if len(draft_ids) != 1 or not draft_ids[0].isdigit():
                raise RuntimeError("PJe returned a draft without one numeric ID")
            cells = row.find_all("td", recursive=False)
            results.append(
                {
                    "draftId": draft_ids[0],
                    "draftUrl": url,
                    "summary": cells[2].get_text(" ", strip=True) if len(cells) > 2 else "",
                }
            )
        return results

    def _pending_row(self, draft_id, author_name):
        matches = [
            row
            for row in self.search_pending_drafts(name=author_name.split()[0][:4])
            if row["draftId"] == str(draft_id)
        ]
        if len(matches) > 1:
            raise RuntimeError("PJe returned multiple rows for one draft ID")
        return matches[0] if matches else None

    def pending_draft(self, draft_id, author_name):
        return {"draftId": draft_id, "inNaoProtocolados": self._pending_row(draft_id, author_name) is not None}

    def pending_draft_url(self, draft_id, author_name):
        row = self._pending_row(draft_id, author_name)
        if row is None:
            raise RuntimeError("The draft was not found in Não Protocolados")
        return row["draftUrl"]
