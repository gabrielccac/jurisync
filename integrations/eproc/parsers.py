"""Small HTML parsers for the JFSC eproc process and event pages."""

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup


BASE_URL = "https://eproc.jfsc.jus.br/eprocV2/"


def text(node):
    return " ".join(node.get_text(" ", strip=True).split()) if node else ""


def _process_row(row):
    cells = row.find_all("td", recursive=False)
    if len(cells) < 8:
        return None
    link = cells[1].select_one('a[href*="acao=processo_selecionar"]')
    if not link:
        return None
    parties = []
    for part in cells[1].select("[data-parte]"):
        name = text(part)
        if name and name not in {p["nome"] for p in parties}:
            parties.append({"parte": part.get("data-parte", "").strip(), "nome": name})
    cell_text = text(cells[1])
    court = re.search(r"Ju[^:]{0,10}:\s*([A-Z0-9]+)", cell_text)
    return {
        "id": (row.select_one("input.infraCheckbox[value]") or {}).get("value"),
        "numero": text(link),
        "url": urljoin(BASE_URL, link.get("href", "")),
        "juizo": court.group(1) if court else "",
        "partes": parties,
        "classe": text(cells[2]),
        "assunto": text(cells[3]),
        "eventoPrazo": text(cells[4]),
        "dataEnvioRequisicao": text(cells[5]),
        "inicioPrazo": text(cells[6]),
        "finalPrazo": text(cells[7]),
        "classeCodigo": row.get("data-classe"),
        "competencia": row.get("data-competencia"),
    }


def parse_process_list(html):
    soup = BeautifulSoup(html, "html.parser")
    return [item for row in soup.select("table.infraTable tr.infraTrClara, table.infraTable tr.infraTrEscura")
            if (item := _process_row(row))]


def _document(anchor):
    title = anchor.get("title", "").replace("\n", " ")
    return {
        "id": anchor.get("data-doc"),
        "nome": anchor.get("data-nome"),
        "tipo": anchor.get("data-mimetype"),
        "url": urljoin(BASE_URL, anchor.get("href", "")),
        "ariaLabel": anchor.get("aria-label", "").strip(),
        "titulo": " ".join(title.split()),
    }


def parse_process_detail(html):
    soup = BeautifulSoup(html, "html.parser")
    result = {"partes": [], "eventos": [], "documentos": []}
    process = soup.select_one("#fldProcesso, #divProcesso")
    heading = soup.select_one("h1, h2, .infraLegendObrigatorio")
    result["titulo"] = text(heading)

    parties_table = soup.select_one("#tblPartesERepresentantes")
    if parties_table:
        role = ""
        column_roles = []
        for row in parties_table.select("tr"):
            header = row.find("th")
            if header:
                headers = row.find_all("th", recursive=False)
                if len(headers) > 1:
                    column_roles = [text(item).strip() for item in headers]
                else:
                    role = text(header).strip()
                continue
            for index, cell in enumerate(row.find_all("td", recursive=False)):
                for part in cell.select("[data-parte]"):
                    name = text(part)
                    if not name:
                        continue
                    cpf = cell.select_one("[id^='spnCpfParte']")
                    representatives = []
                    for sr in cell.select(".sr-only"):
                        value = text(sr)
                        if "Tipo de Usu" in value:
                            representatives.append(value.split(":", 1)[-1].strip())
                    result["partes"].append({
                        "papel": part.get("data-parte", role).strip(),
                        "grupo": column_roles[index] if index < len(column_roles) else role,
                        "nome": name,
                        "documento": text(cpf),
                        "representantes": representatives,
                    })

    events = soup.select("#tblEventos tr[id^='trEvento']")
    for row in events:
        cells = row.find_all("td", recursive=False)
        if len(cells) < 5:
            continue
        docs = [_document(a) for a in cells[4].select("a.infraLinkDocumento[data-doc]")]
        user = cells[3].select_one("label.infraEventoUsuario")
        label = cells[2].select_one("label.infraEventoDescricao")
        event = {
            "id": re.sub(r"\D", "", row.get("id", "")),
            "dataHora": text(cells[1]),
            "label": text(label) or text(cells[2]),
            "descricao": text(cells[2]),
            "usuario": text(user),
            "usuarioDetalhes": user.get("aria-label", "").replace("<br>", " ").strip() if user else "",
            "parte": row.get("data-parte"),
            "classes": row.get("class", []),
            "tipo": "tarefa" if any("Prazo" in c for c in row.get("class", [])) else "evento",
            "documentos": docs,
        }
        result["eventos"].append(event)
        result["documentos"].extend(docs)

    match = re.search(r"\d{7}-\d{2}\.\d{4}\.\d+\.\d{2}\.\d{4}", soup.get_text(" ", strip=True))
    result["numero"] = match.group(0) if match else None
    result["documentos"] = list({doc["id"]: doc for doc in result["documentos"]}.values())
    result["eventCount"] = len(result["eventos"])
    result["documentCount"] = len(result["documentos"])
    return result
