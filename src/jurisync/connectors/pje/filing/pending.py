"""Não Protocolados: find drafts and confirm one is listed."""

import re
from urllib.parse import parse_qs, urljoin, urlsplit

from bs4 import BeautifulSoup

from jurisync.connectors.pje import jsf
from jurisync.connectors.pje.filing.constants import PENDING_URL
from jurisync.connectors.pje.filing.parsing import name_matches
from jurisync.connectors.pje.filing.payload import masked_cpf


class PendingMixin:
    def _pending_results(self, name=None, cpf=None):
        """Run the Não Protocolados search and return the page that lists the results."""
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
        return self.ajax(PENDING_URL, jsf.view_state(html), fields, container, action)

    def search_pending_drafts(self, *, name=None, cpf=None):
        html = self._pending_results(name, cpf)
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

    @staticmethod
    def _draft_rows(html, draft_id):
        table = BeautifulSoup(html, "html.parser").find(id="consultaProcessoNaoProtocoladoGridList")
        if table is None:
            raise RuntimeError("PJe did not render the unfiled-process results")
        rows = []
        for row in table.find_all("tr"):
            for link in row.find_all("a", href=True):
                if parse_qs(urlsplit(link["href"]).query).get("idProcesso") == [str(draft_id)]:
                    rows.append(row)
                    break
        return rows

    def delete_draft(self, draft_id, author_name):
        """Remove one draft from Não Protocolados with the row's own Remover link. This cannot be undone.

        The row is found by draft number, must show the given author, and the draft must be gone from a fresh search.
        """
        draft_id = str(draft_id)
        if not draft_id.isdigit():
            raise ValueError("draft_id must be the numeric draft number")
        if not author_name or not author_name.strip():
            raise ValueError("The author's name is required to delete a draft")
        search_term = author_name.split()[0][:4]
        html = self._pending_results(name=search_term)
        rows = self._draft_rows(html, draft_id)
        if len(rows) != 1:
            raise RuntimeError(f"Expected one draft {draft_id} in Não Protocolados, found {len(rows)}")
        row = rows[0]
        if not name_matches(row.get_text(" ", strip=True), author_name):
            raise RuntimeError(f"Draft {draft_id} does not belong to {author_name}; nothing was deleted")
        links = [link for link in row.find_all("a") if (link.get("title") or "").strip() == "Remover"]
        form = links[0].find_parent("form") if len(links) == 1 else None
        if form is None:
            raise RuntimeError("PJe did not render one Remover action for this draft")
        container, action = jsf.ajax_event(links[0])
        # The paging values are unquoted numbers in the link, which ajax_event does not read.
        parameters = re.search(r"'parameters':\{([^}]*)\}", links[0].get("onclick", ""))
        action.update(dict(re.findall(r"'(\w+)':(\d+)", parameters.group(1) if parameters else "")))
        self.ajax(PENDING_URL, jsf.view_state(html), jsf.form_fields(html, form["id"]), container, action)
        if self._draft_rows(self._pending_results(name=search_term), draft_id):
            raise RuntimeError(f"PJe still lists draft {draft_id} after the delete")
        return {"draftId": draft_id, "deleted": True}
