"""Parsers of the ``tjgo-servlet`` Projudi generation (HTML pages and AJAX JSON).

Projudi serves servlet-rendered HTML with no stable contract, so every parser fails loudly
(``ProjudiParseError``) when the structure it expects is missing, instead of returning an empty list that
would read as "no movements" or "no documents".
"""

import json
import re

from bs4 import BeautifulSoup

from jurisync.connectors.projudi.base import DOCUMENT_HASH, DOCUMENT_ID, ProjudiParseError

PROCESS_TITLE = "dados processo"
PARTY_POLES = ("polo ativo", "polo passivo", "outras partes", "terceiro")


def _clean(value):
    return " ".join((value or "").split())


def decode(content):
    """Projudi pages and JSON are ISO-8859-1 unless they declare otherwise."""
    if isinstance(content, str):
        return content
    for encoding in ("utf-8", "iso-8859-1"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("iso-8859-1", "replace")


def page_title(html):
    soup = BeautifulSoup(html, "html.parser")
    return _clean(soup.title.get_text(" ")) if soup.title else ""


def is_process_page(html):
    return page_title(html).casefold() == PROCESS_TITLE


def _labelled_values(fieldset):
    """``<div>Label</div><span>Value</span>`` pairs of a Projudi detail block."""
    values = {}
    for label in fieldset.find_all("div", recursive=False):
        name = _clean(label.get_text(" "))
        value = label.find_next_sibling("span")
        if name and value is not None and name not in values:
            values[name] = _clean(value.get_text(" "))
    return values


def _parties(soup):
    parties = []
    for block in soup.find_all("fieldset", id="VisualizaDados"):
        legend = block.find("legend", recursive=False)
        title = _clean(legend.get_text(" ")) if legend else ""
        if not title.casefold().startswith(PARTY_POLES):
            continue
        for party in block.find_all("fieldset", class_="VisualizaDados"):
            name = party.find("span", attrs={"title": "Nome da Parte"})
            if name is not None:
                parties.append({"role": title, "name": _clean(name.get_text(" "))})
    return parties


def _movements(soup):
    table = soup.find("table", id="TabelaArquivos")
    if table is None:
        raise ProjudiParseError("Projudi process page has no movements table")
    movements = []
    for row in table.find_all("tr"):
        if not row.get("movi_codigo"):
            continue
        cells = row.find_all("td")
        if len(cells) < 4:
            raise ProjudiParseError("Projudi movement row has an unexpected shape")
        kind = cells[1].find("span", class_="filtro_tipo_movimentacao")
        description = _clean(cells[1].get_text(" "))
        title = _clean(kind.get_text(" ")) if kind else ""
        hidden = row.find_next_sibling("tr")
        movement_id = (hidden.get("id") or "").removeprefix("linha_") if hidden is not None else ""
        movements.append(
            {
                "sequence": _clean(cells[0].get_text(" ")),
                "movementId": movement_id if movement_id.isdigit() else "",
                "type": title,
                "description": _clean(description.removeprefix(title)),
                "date": _clean(cells[2].get_text(" ")),
                "user": _clean(cells[3].get_text(" ")),
                "hasFiles": row.find("a", href=re.compile("buscarArquivosMovimentacaoJSON")) is not None,
            }
        )
    return movements


def parse_process(html):
    """Number, area, case data, parties and the movements table of a ``Dados Processo`` page."""
    html = decode(html)
    if not is_process_page(html):
        raise ProjudiParseError("Projudi did not return a process page")
    soup = BeautifulSoup(html, "html.parser")
    number = soup.find(id="span_proc_numero")
    if number is None:
        raise ProjudiParseError("Projudi process page has no process number")
    header = number.find_parent("div", class_="aEsquerda")
    area = ""
    if header is not None:
        area = _clean(header.get_text(" ")).split(_clean(number.get_text(" ")), 1)[-1]
        area = _clean(re.sub(r"^\s*[Áá]rea\s*", "", area))
    other = {}
    for block in soup.find_all("fieldset", id="VisualizaDados"):
        legend = block.find("legend", recursive=False)
        if legend is not None and _clean(legend.get_text(" ")) == "Outras Informações":
            other = _labelled_values(block)
    movements = _movements(soup)
    if not movements:
        raise ProjudiParseError("Projudi process page lists no movements")
    return {
        "number": _clean(number.get_text(" ")),
        "area": area,
        "court": other.get("Serventia", ""),
        "class": other.get("Classe", ""),
        "subjects": other.get("Assunto(s)", ""),
        "claimValue": other.get("Valor da Causa", ""),
        "phase": other.get("Fase Processual", ""),
        "parties": _parties(soup),
        "movements": movements,
    }


def _json(content, label):
    try:
        data = json.loads(decode(content))
    except ValueError:
        raise ProjudiParseError(f"Projudi {label} is not JSON") from None
    if not isinstance(data, list):
        raise ProjudiParseError(f"Projudi {label} has an unexpected shape")
    return data


def _file(item):
    identifier, hash_value = str(item.get("id_movi_arq") or ""), str(item.get("hash") or "")
    if not DOCUMENT_ID.fullmatch(identifier) or not DOCUMENT_HASH.fullmatch(hash_value):
        raise ProjudiParseError("Projudi file entry has no usable id and hash")
    return {"id": identifier, "name": _clean(item.get("nome")), "hash": hash_value}


def parse_index(content):
    """The full document index: ``[{title, complement, files: [{id, name, hash}]}]``, one entry per movement.

    ``arquivos`` is null for a movement without attachments and a list of 1..N otherwise.
    """
    movements = []
    for item in _json(content, "document index"):
        if not isinstance(item, dict) or "titulo" not in item:
            raise ProjudiParseError("Projudi document index entry has an unexpected shape")
        files = [_file(file) for file in item.get("arquivos") or []]
        movements.append(
            {"title": _clean(item["titulo"]), "complement": _clean(item.get("complemento")), "files": files}
        )
    return movements


def parse_movement_files(content):
    """Attachments of one movement with their metadata. A file is released when the user's access level reaches it."""
    files = []
    for item in _json(content, "movement files"):
        if not isinstance(item, dict):
            raise ProjudiParseError("Projudi movement file entry has an unexpected shape")
        identifier = str(item.get("id_movi_arq") or item.get("id") or "")
        hash_value = str(item.get("hash") or "")
        if not DOCUMENT_ID.fullmatch(identifier) or not DOCUMENT_HASH.fullmatch(hash_value):
            raise ProjudiParseError("Projudi movement file has no usable id and hash")
        try:
            released = int(item.get("AcessoUsuario")) >= int(item.get("AcessoArquivo"))
        except (TypeError, ValueError):
            released = None
        files.append(
            {
                "id": identifier,
                "name": _clean(item.get("nome_arquivo")),
                "type": _clean(item.get("arquivo_tipo")),
                "signedBy": _clean(item.get("usuario_assinador")),
                "released": released,
                "hash": hash_value,
            }
        )
    return files


def align_index(index, movements):
    """Attach each index entry to its process-page movement.

    The index is oldest first and the page newest first; the two are paired only when they have the same length and
    every title matches, so a layout change never attaches files to the wrong movement.
    """
    if len(index) != len(movements) or any(
        entry["title"] != movement["type"] for entry, movement in zip(index, reversed(movements), strict=True)
    ):
        return None
    return [{**movement, "files": entry["files"]} for entry, movement in zip(index, reversed(movements), strict=True)][
        ::-1
    ]
