"""Render an audited petition to an editable .docx and, from it, a PDF."""

import re
import shutil
import subprocess
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK, WD_COLOR_INDEX
from docx.shared import Cm, Pt

from jurisync.workflows.petitions.preflight import ROMAN, UNNUMBERED

PENDING = re.compile(r"\[PENDENTE:[^\]]*\]")
IMAGE = re.compile(r"\[\[IMG\|([^|\]]*)\|([^\]]*)\]\]")
NUMBERED_TITLE = re.compile(r"^[IVX]+\s*[—–-]\s*")
TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
TABLE_RULE = re.compile(r"^\s*\|?[\s:|-]+\|?\s*$")
CENTERED = {"qual.titulo_acao"}
FOOTER = {"fecho.rodape_escritorio"}


def _paragraph(document, text, *, bold=False, center=False, indent=True):
    paragraph = document.add_paragraph()
    position = 0
    for match in PENDING.finditer(text):
        paragraph.add_run(text[position : match.start()]).bold = bold
        mark = paragraph.add_run(match.group(0))
        mark.bold = True
        mark.font.highlight_color = WD_COLOR_INDEX.YELLOW
        position = match.end()
    paragraph.add_run(text[position:]).bold = bold
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER if center else WD_ALIGN_PARAGRAPH.JUSTIFY
    if indent and not center:
        paragraph.paragraph_format.first_line_indent = Cm(2)
    paragraph.paragraph_format.space_after = Pt(8)
    paragraph.paragraph_format.keep_with_next = bold and not center
    return paragraph


def _open_items_page(document, assembled):
    """The list at the end of a draft with open items. It is not part of the petition and must leave before filing."""
    items = assembled.get("pendencias") or {}
    lines = [f"{item['descricao']}. Resolve: {item['quem_resolve']}." for item in items.get("faltas") or []]
    lines += [f"Conflito: {item.get('descricao', item.get('campo', ''))}." for item in items.get("conflitos") or []]
    lines += [f"Aviso: {item}" for item in items.get("avisos_fortes") or []]
    if not lines and not items.get("marcadas_no_texto"):
        return
    document.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
    _paragraph(document, "PENDÊNCIAS DESTA MINUTA (retirar esta página antes do protocolo)", bold=True, center=True)
    if items.get("confirmado_por"):
        _paragraph(document, f"Seguiu com pendências por decisão de: {items['confirmado_por']}.", indent=False)
    for line in lines or items["marcadas_no_texto"]:
        _paragraph(document, f"- {line}", indent=False)


def _table(document, rows):
    cells = [[cell.strip() for cell in row.strip().strip("|").split("|")] for row in rows if not TABLE_RULE.match(row)]
    table = document.add_table(rows=len(cells), cols=max(len(row) for row in cells))
    table.style = "Table Grid" if "Table Grid" in [style.name for style in document.styles] else None
    for row_index, row in enumerate(cells):
        for column, value in enumerate(row):
            table.cell(row_index, column).text = value
    document.add_paragraph()


def _write_line(document, line, *, center, bold, indent, prefix=""):
    position = 0
    for match in IMAGE.finditer(line):
        if line[position : match.start()].strip():
            _paragraph(
                document, prefix + line[position : match.start()].strip(), center=center, bold=bold, indent=indent
            )
            prefix = ""
        _image(document, match.group(1), match.group(2))
        position = match.end()
    rest = line[position:].strip()
    if rest:
        _paragraph(document, prefix + rest, center=center, bold=bold, indent=indent)


def _write_text(document, item, prefix=""):
    section = item["secao"]
    center = section == "enderecamento" or item["id"] in CENTERED
    bold = center
    rows = []
    for line in filter(None, (part.strip() for part in item["texto"].split("\n"))):
        if TABLE_ROW.match(line):
            rows.append(line)
            continue
        if rows:
            _table(document, rows)
            rows = []
        _write_line(
            document,
            line.upper() if section == "enderecamento" else line,
            center=center or section == "fecho",
            bold=bold,
            indent=section not in ("fecho", "pedido"),
            prefix=prefix,
        )
        prefix = ""
    if rows:
        _table(document, rows)


def _image(document, path, caption):
    if not Path(path).is_file():
        raise ValueError("Imagem indicada no dossiê não foi encontrada.")
    document.add_picture(path, width=Cm(14))
    document.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
    if caption:
        _paragraph(document, caption, center=True)


def to_docx(assembled, section_titles, output, *, template=None, footer=None):
    if not assembled["ok"]:
        raise ValueError("A peça tem problemas na auditoria e não pode ser gerada.")
    document = Document(template) if template else Document()
    if not template:
        style = document.styles["Normal"]
        style.font.name = "Times New Roman"
        style.font.size = Pt(12)
    for item in assembled["itens"]:
        if item["id"] in FOOTER:
            footer = footer or item["texto"]
    if footer:
        document.sections[0].footer.paragraphs[0].text = footer
    current, numeral, subtitle, letter = None, 0, 0, 0
    for item in assembled["itens"]:
        section = item["secao"]
        if item["id"] in FOOTER:
            continue
        if section != current:
            current, subtitle, letter = section, 0, 0
            if section not in UNNUMBERED:
                title = NUMBERED_TITLE.sub("", section_titles[section]).upper()
                _paragraph(document, f"{ROMAN[numeral]} – {title}", bold=True, indent=False)
                numeral += 1
        if item["imprime_titulo"] and section not in UNNUMBERED:
            subtitle += 1
            _paragraph(document, f"{subtitle}. {item['titulo']}", bold=True, indent=False)
        prefix = ""
        if section == "pedido" and item["id"] != "pedido.abertura":
            prefix = f"{chr(97 + letter)}) "
            letter += 1
        _write_text(document, item, prefix)
    _open_items_page(document, assembled)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    document.save(output)
    return output


def to_pdf(docx_path, folder=None):
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        raise RuntimeError("LibreOffice não está instalado; o .docx foi gerado, o PDF não.")
    docx_path = Path(docx_path)
    folder = Path(folder or docx_path.parent)
    subprocess.run(
        [soffice, "--headless", "--convert-to", "pdf", "--outdir", str(folder), str(docx_path)],
        check=True,
        capture_output=True,
        timeout=180,
    )
    return folder / f"{docx_path.stem}.pdf"
