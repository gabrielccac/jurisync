"""Draft subjects (assuntos): search and association."""

import re

from bs4 import BeautifulSoup

from jurisync.connectors.pje import jsf
from jurisync.connectors.pje.filing.constants import UPDATE_URL


def subject_rows(table):
    for row in table.find_all("tr") if table else []:
        cells = [cell.get_text(" ", strip=True) for cell in row.find_all("td", recursive=False)]
        if len(cells) >= 4 and re.fullmatch(r"\d+", cells[1]):
            yield row, cells[1], cells[2], cells[3] == "Sim"


def has_subject_code(table, code):
    for row in table.find_all("tr") if table else []:
        cells = row.find_all("td", recursive=False)
        if len(cells) > 1 and cells[1].get_text(" ", strip=True) == code:
            return True
    return False


class SubjectsMixin:
    def search_subjects(self, draft_url, search_term, page=1):
        """List PJe assunto choices without associating one with the draft."""
        if not search_term.strip() or page < 1:
            raise ValueError("Subject search needs text and a positive page number")
        html = self.get(draft_url)
        soup = BeautifulSoup(html, "html.parser")
        fields = jsf.form_fields(html, "r_processoAssuntoListSearchForm")
        query = jsf.field(soup, "assuntoCompleto")
        fields[query["name"]] = search_term.strip()
        fields["r_processoAssuntoListSearchForm:page"] = str(page)
        fields["r_processoAssuntoListSearchForm:searching"] = "true"
        container, action = jsf.ajax_event(jsf.field(soup, "r_processoAssuntoListSearchForm:search"))
        html = self.ajax(UPDATE_URL, jsf.view_state(html), fields, container, action)
        soup = BeautifulSoup(html, "html.parser")
        table = soup.find(id="r_processoAssuntoListList")
        if table is None:
            raise RuntimeError("PJe did not render the subject search results")
        results = []
        for _, code, full_path, complementary in subject_rows(table):
            name = re.sub(rf"\s*\({code}\)$", "", full_path.split("|")[-1]).strip()
            results.append({"code": code, "name": name, "path": full_path, "complementary": complementary})
        count = re.search(r"(\d+)\s+resultados?\s+encontrados?", soup.get_text(" ", strip=True), re.I)
        return {
            "search": search_term.strip(),
            "page": page,
            "total": int(count.group(1)) if count else None,
            "results": results,
        }

    def add_subjects(self, draft_url, subjects):
        """Associate every subject; the draft page is read once and again only after a change."""
        page, results = None, []
        for subject in subjects:
            page = page if page is not None else self.get(draft_url)
            result = self.add_subject(draft_url, subject, page=page)
            if not result["alreadyAdded"]:
                page = None
            results.append(result)
        return results

    def add_subject(self, draft_url, subject, page=None):
        html = page if page is not None else self.get(draft_url)
        soup = BeautifulSoup(html, "html.parser")
        associated = soup.find(id="l_processoAssuntoListList")
        already_added = has_subject_code(associated, subject["code"])
        if already_added:
            return {"alreadyAdded": True, "subjectCode": subject["code"]}
        fields = jsf.form_fields(html, "r_processoAssuntoListSearchForm")
        query = jsf.field(soup, "assuntoCompleto")
        fields[query["name"]] = subject["search"]
        fields["r_processoAssuntoListSearchForm:page"] = "1"
        fields["r_processoAssuntoListSearchForm:searching"] = "true"
        search = jsf.field(soup, "r_processoAssuntoListSearchForm:search")
        container, action = jsf.ajax_event(search)
        html = self.ajax(UPDATE_URL, jsf.view_state(html), fields, container, action)
        soup = BeautifulSoup(html, "html.parser")
        table = soup.find(id="r_processoAssuntoListList")
        matches = [
            row
            for row, code, full_path, _ in subject_rows(table)
            if code == subject["code"] and subject["name"] in full_path
        ]
        if len(matches) != 1:
            raise RuntimeError(f"Subject lookup returned {len(matches)} matching rows")
        link = matches[0].find("a", onclick=True)
        form = link.find_parent("form") if link else None
        if form is None:
            raise RuntimeError("PJe did not render the subject selection action")
        container, action = jsf.ajax_event(link)
        html = self.ajax(UPDATE_URL, jsf.view_state(html), jsf.form_fields(html, form["id"]), container, action)
        associated = BeautifulSoup(html, "html.parser").find(id="l_processoAssuntoListList")
        if not has_subject_code(associated, subject["code"]):
            raise RuntimeError("PJe did not show the subject on the draft")
        return {"alreadyAdded": False, "subjectCode": subject["code"]}
