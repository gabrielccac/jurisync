"""The draft as the drafter writes it: plain text with a few line marks, read into styled paragraphs.

One line is one paragraph. A line starts with:

- ``@enderecamento``, ``@titulo``, ``@fecho``, ``@assinatura``: those parts of the petition;
- ``# `` a numbered section ("I – DOS FATOS"), ``## `` a subtitle ("1. Do atraso");
- ``> `` a literal quotation (ementa), ``- `` a request (lettered a), b)... from each section on);
- ``![caption](file)`` a proof image (PDF pages too); ``|`` a table row;
- anything else: a body paragraph.

Inline: ``**bold**``, ``*italic*``, ``__underline__``; ``[PENDENTE: what is missing]`` is highlighted.
"""

import re

PENDING = re.compile(r"\[PENDENTE:[^\]]*\]")
IMAGE = re.compile(r"^!\[(.*)\]\(([^()\s]+)\)\s*$")
TABLE_RULE = re.compile(r"^\s*\|?[\s:|-]+\|?\s*$")
INLINE = re.compile(r"(\*\*.+?\*\*|__.+?__|(?<!\*)\*(?!\*).+?(?<!\*)\*(?!\*)|\[PENDENTE:[^\]]*\])")
MARKS = {
    "@enderecamento": "enderecamento",
    "@titulo": "titulo",
    "@fecho": "fecho",
    "@assinatura": "assinatura",
}


def parse(text):
    """Paragraphs of the draft: ``{"tipo", "texto"}``, ``{"tipo": "imagem", "arquivo", "legenda"}``, tables."""
    items, rows = [], []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if line.startswith("|"):
            if not TABLE_RULE.match(line):
                rows.append([cell.strip() for cell in line.strip("|").split("|")])
            continue
        if rows:
            items.append({"tipo": "tabela", "linhas": rows})
            rows = []
        if not line or line.startswith("%%"):  # %% is a note to the reviewer, never printed
            continue
        image = IMAGE.match(line)
        if image:
            items.append({"tipo": "imagem", "legenda": image.group(1).strip(), "arquivo": image.group(2).strip()})
            continue
        mark, _, rest = line.partition(" ")
        if mark.casefold() in MARKS:
            items.append({"tipo": MARKS[mark.casefold()], "texto": rest.strip()})
        elif line.startswith("## "):
            items.append({"tipo": "subtitulo", "texto": line[3:].strip()})
        elif line.startswith("# "):
            items.append({"tipo": "secao", "texto": line[2:].strip()})
        elif line.startswith("> "):
            items.append({"tipo": "citacao", "texto": line[2:].strip()})
        elif line.startswith("- "):
            items.append({"tipo": "pedido", "texto": line[2:].strip()})
        else:
            items.append({"tipo": "corpo", "texto": line})
    if rows:
        items.append({"tipo": "tabela", "linhas": rows})
    return items


def runs(text):
    """``(text, bold, italic, underline, pending)`` pieces of one paragraph."""
    pieces, position = [], 0
    for match in INLINE.finditer(text):
        if match.start() > position:
            pieces.append((text[position : match.start()], False, False, False, False))
        token = match.group(0)
        if token.startswith("[PENDENTE:"):
            pieces.append((token, True, False, False, True))
        elif token.startswith("**"):
            pieces.append((token[2:-2], True, False, False, False))
        elif token.startswith("__"):
            pieces.append((token[2:-2], False, False, True, False))
        else:
            pieces.append((token[1:-1], False, True, False, False))
        position = match.end()
    if position < len(text):
        pieces.append((text[position:], False, False, False, False))
    return pieces


def plain(item):
    """The visible text of a paragraph, without the inline marks (what the court will read)."""
    if item["tipo"] == "tabela":
        return "\n".join(" | ".join(row) for row in item["linhas"])
    if item["tipo"] == "imagem":
        return item.get("legenda") or ""
    return "".join(piece[0] for piece in runs(item.get("texto") or ""))


def pending_marks(items):
    marks = []
    for item in items:
        for mark in PENDING.findall(plain(item)):
            if mark not in marks:
                marks.append(mark)
    return marks
