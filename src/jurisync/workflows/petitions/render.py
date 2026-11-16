"""Write an audited draft into the office's Word template (.docx, editable) and, from it, a PDF."""

import subprocess
import tempfile
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK, WD_COLOR_INDEX
from docx.shared import Cm, Pt, RGBColor

from jurisync.core.word_to_pdf import convert_word_to_pdf
from jurisync.workflows.petitions import minuta

# Paragraph styles of the office template; a document without them falls back to Normal with direct formatting.
STYLES = {
    "enderecamento": "Enderecamento",
    "titulo": "Titulo da Acao",
    "secao": "Secao",
    "subtitulo": "Subtitulo",
    "citacao": "Citacao",
    "pedido": "Pedido",
    "fecho": "Fecho",
    "assinatura": "Assinatura",
    "corpo": "Corpo",
    "legenda": "Legenda",
    "imagem": "Imagem",
    "pendencias": "Pendencias",
}
MAX_WIDTH, MAX_HEIGHT = 14, 11  # cm: a phone print keeps its proportion without taking a whole page
RED = RGBColor(0x9C, 0x00, 0x06)


def _style(document, kind):
    name = STYLES.get(kind, "Normal")
    return name if name in [style.name for style in document.styles] else "Normal"


def _fallback(paragraph, kind):
    """Direct formatting when the document has no office styles (a draft without the template)."""
    if paragraph.style.name != "Normal":
        return
    if kind in ("enderecamento", "titulo", "secao", "subtitulo", "assinatura", "pendencias"):
        for run in paragraph.runs:
            run.bold = True
    if kind in ("enderecamento", "titulo", "fecho", "assinatura", "legenda", "imagem", "pendencias"):
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    else:
        paragraph.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    if kind == "corpo":
        paragraph.paragraph_format.first_line_indent = Cm(1.5)
    if kind == "citacao":
        paragraph.paragraph_format.left_indent = Cm(4)
        for run in paragraph.runs:
            run.italic = True


def _paragraph(document, kind, text):
    paragraph = document.add_paragraph(style=_style(document, kind))
    for piece, bold, italic, underline, pending in minuta.runs(text):
        run = paragraph.add_run(piece)
        run.bold = bold or None
        run.italic = italic or None
        run.underline = underline or None
        if pending:
            run.font.highlight_color = WD_COLOR_INDEX.YELLOW
            run.font.color.rgb = RED
    _fallback(paragraph, kind)
    return paragraph


def _table(document, rows):
    table = document.add_table(rows=len(rows), cols=max(len(row) for row in rows))
    table.autofit = True
    if "Table Grid" in [style.name for style in document.styles]:
        table.style = "Table Grid"
    for row_index, row in enumerate(rows):
        for column, value in enumerate(row):
            cell = table.cell(row_index, column)
            cell.text = ""
            paragraph = cell.paragraphs[0]
            paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT
            paragraph.paragraph_format.first_line_indent = Cm(0)
            paragraph.paragraph_format.left_indent = Cm(0)
            for piece, bold, italic, underline, _ in minuta.runs(value):
                run = paragraph.add_run(piece)
                run.bold = True if row_index == 0 else bold or None  # the first row is the header
                run.italic, run.underline = italic or None, underline or None
                run.font.size = Pt(10)
    document.add_paragraph()


def image_file(path, root):
    """The image inside the case folder. Paths come from the draft, so no other local file may be embedded."""
    name = Path(path).name
    if root is None:
        raise ValueError(f"Imagem {name}: informe a pasta do caso para incluir imagens.")
    root = Path(root).resolve()
    file = (root / path).resolve()  # follows .. and symbolic links; an absolute path replaces the root
    if not file.is_relative_to(root):
        raise ValueError(f"Imagem {name}: arquivo fora da pasta do caso não pode ser incluído.")
    if not file.is_file():
        raise ValueError(f"Imagem {name}: arquivo não encontrado na pasta do caso.")
    return file


def _pdf_pages(file, folder):
    """Each page of a PDF as PNG without its blank margins, so a PDF proof shows like any print."""
    subprocess.run(
        ["pdftoppm", "-png", "-r", "110", str(file), str(Path(folder) / "pagina")],
        check=True,
        capture_output=True,
        timeout=120,
    )
    pages = sorted(Path(folder).glob("pagina*.png"))
    for page in pages:
        _trim(page)
    return pages


def _trim(file):
    from PIL import Image, ImageOps

    with Image.open(file) as picture:
        box = ImageOps.invert(picture.convert("L")).point(lambda value: 255 if value > 20 else 0).getbbox()
        if box:
            left, top, right, bottom = box
            margin = 20
            picture.crop(
                (
                    max(left - margin, 0),
                    max(top - margin, 0),
                    min(right + margin, picture.width),
                    min(bottom + margin, picture.height),
                )
            ).save(file)


def _add_picture(document, file):
    from PIL import Image

    with Image.open(file) as picture:
        width, height = picture.size
    paragraph = document.add_paragraph(style=_style(document, "imagem"))
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = paragraph.add_run()
    if height / width > MAX_HEIGHT / MAX_WIDTH:
        run.add_picture(str(file), height=Cm(MAX_HEIGHT))
    else:
        run.add_picture(str(file), width=Cm(MAX_WIDTH))


def _image(document, item, root):
    file = image_file(item["arquivo"], root)
    if file.suffix.casefold() == ".pdf":
        with tempfile.TemporaryDirectory() as folder:
            try:
                pages = _pdf_pages(file, folder)
            except (subprocess.SubprocessError, OSError) as error:
                raise ValueError(f"Imagem {file.name}: não foi possível converter o PDF.") from error
            for page in pages:
                _add_picture(document, page)
    else:
        _add_picture(document, file)
    if item.get("legenda"):
        _paragraph(document, "legenda", item["legenda"])


def _open_items_page(document, marks, open_items, confirmed_by):
    """The list at the end of a draft with open items. It is not part of the petition and leaves before filing."""
    if not marks and not open_items:
        return
    document.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
    _paragraph(document, "pendencias", "PENDÊNCIAS DESTA MINUTA (retirar esta página antes do protocolo)")
    if confirmed_by:
        _paragraph(document, "corpo", f"Seguiu com pendências por decisão de: {confirmed_by}.")
    for mark in marks:
        _paragraph(document, "pedido", f"No texto: {mark[len('[PENDENTE:') : -1].strip()}")
    groups = {}
    for item in open_items:  # {"item": ..., "quem": "cliente|colaborador|advogada"} or plain text
        who = item.get("quem") if isinstance(item, dict) else None
        text = str(item.get("item") if isinstance(item, dict) else item).strip()
        groups.setdefault(who or "", []).append(text if text.endswith(("?", "!", ".")) else text + ".")
    for who, texts in groups.items():
        if who:
            _paragraph(document, "subtitulo", f"Resolve: {who}")
        for text in texts:
            _paragraph(document, "pedido", text)


def to_docx(items, output, *, template=None, image_root=None, open_items=(), confirmed_by=None):
    """Write the draft paragraphs into a copy of the template; the template's own body text is dropped."""
    document = Document(template) if template else Document()
    body = document.element.body
    for element in list(body):
        if element.tag.endswith("}sectPr"):
            continue
        body.remove(element)
    if not template:
        style = document.styles["Normal"]
        style.font.name, style.font.size = "Times New Roman", Pt(12)
    letter = 0
    for item in items:
        kind = item["tipo"]
        if kind == "secao":
            letter = 0
        if kind == "imagem":
            _image(document, item, image_root)
        elif kind == "tabela":
            _table(document, item["linhas"])
        elif kind == "pedido":
            _paragraph(document, kind, f"{chr(97 + letter % 26)}) {item['texto']}")
            letter += 1
        else:
            paragraph = _paragraph(document, kind, item["texto"])
            if kind == "fecho":  # the closing stays on the page of the signature
                paragraph.paragraph_format.keep_with_next = True
    _open_items_page(document, minuta.pending_marks(items), list(open_items), confirmed_by)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    document.save(output)
    return output


def to_pdf(docx_path, folder=None):
    """Convert the .docx with the shared converter (isolated LibreOffice profile, output checked)."""
    docx_path = Path(docx_path)
    destination = Path(folder or docx_path.parent) / f"{docx_path.stem}.pdf"
    try:
        return convert_word_to_pdf(docx_path, destination)
    except (subprocess.SubprocessError, OSError) as error:
        # The exception text carries the command line and temporary paths; keep them out of what is shown.
        raise RuntimeError("O LibreOffice não conseguiu converter; o .docx foi gerado, o PDF não.") from error
