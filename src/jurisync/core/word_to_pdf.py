"""Convert Word documents to PDF with the LibreOffice runtime."""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from xml.dom import Node, minidom

WORD = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
VOLATILE = re.compile(r"^\s*(?:DATE|TIME)\b", re.I)


def _elements(node):
    for child in node.childNodes:
        if child.nodeType == Node.ELEMENT_NODE:
            yield child
            yield from _elements(child)


def _text(node):
    return "".join(child.data for child in node.childNodes if child.nodeType == Node.TEXT_NODE)


def freeze_xml(data):
    document = minidom.parseString(data)
    stack = []
    remove = []
    simple = []
    frozen = 0
    for node in _elements(document):
        if node.namespaceURI != WORD:
            continue
        if node.localName == "fldSimple" and VOLATILE.match(node.getAttributeNS(WORD, "instr")):
            if not any(
                _text(item).strip() for item in _elements(node) if item.namespaceURI == WORD and item.localName == "t"
            ):
                raise ValueError("DATE/TIME field has no cached text")
            simple.append(node)
        elif node.localName == "fldChar":
            kind = node.getAttributeNS(WORD, "fldCharType")
            if kind == "begin":
                stack.append(
                    {
                        "begin": node,
                        "instruction": [],
                        "instructionNodes": [],
                        "separator": None,
                        "cached": [],
                        "after": False,
                    }
                )
            elif kind == "separate" and stack:
                stack[-1]["separator"] = node
                stack[-1]["after"] = True
            elif kind == "end" and stack:
                field = stack.pop()
                if VOLATILE.match("".join(field["instruction"])):
                    if not field["separator"] or not "".join(field["cached"]).strip():
                        raise ValueError("DATE/TIME field has no cached text")
                    remove.extend([field["begin"], *field["instructionNodes"], field["separator"], node])
                    frozen += 1
        elif node.localName == "instrText" and stack and not stack[-1]["after"]:
            stack[-1]["instruction"].append(_text(node))
            stack[-1]["instructionNodes"].append(node)
        elif node.localName == "t" and stack and stack[-1]["after"]:
            stack[-1]["cached"].append(_text(node))
    if any(VOLATILE.match("".join(field["instruction"])) for field in stack):
        raise ValueError("Unclosed DATE/TIME field")
    if not frozen and not simple:
        return data, 0
    for node in remove:
        node.parentNode.removeChild(node)
    for node in simple:
        parent = node.parentNode
        for child in list(node.childNodes):
            parent.insertBefore(child, node)
        parent.removeChild(node)
    return document.toxml(encoding="utf-8"), frozen + len(simple)


def freeze_docx(source, destination):
    frozen = 0
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(destination, "w") as output:
        for item in original.infolist():
            data = original.read(item.filename)
            if item.filename.startswith("word/") and item.filename.endswith(".xml"):
                data, count = freeze_xml(data)
                frozen += count
            output.writestr(item, data)
    return frozen


def convert_word_to_pdf(source, destination):
    """Render a DOC/DOCX with isolated LibreOffice; return the output PDF path."""
    source = Path(source).resolve(strict=True)
    destination = Path(destination).resolve()
    if source.suffix.lower() not in {".doc", ".docx"} or destination.suffix.lower() != ".pdf":
        raise ValueError("Expected a DOC/DOCX source and a PDF destination")
    office = shutil.which(os.environ.get("LIBREOFFICE_BIN", "libreoffice"))
    if not office:
        raise RuntimeError("LibreOffice runtime is missing; install libreoffice-writer in the service image")
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(prefix="jurisync-word-pdf-") as folder:
        work = Path(folder)
        prepared = source
        if source.suffix.lower() == ".docx":
            prepared = work / source.name
            freeze_docx(source, prepared)
        result = work / (source.stem + ".pdf")
        subprocess.run(
            [
                office,
                "-env:UserInstallation=" + (work / "profile").as_uri(),
                "--headless",
                "--convert-to",
                "pdf:writer_pdf_Export",
                "--outdir",
                str(work),
                str(prepared),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if not result.is_file():
            raise RuntimeError("LibreOffice did not produce a valid PDF")
        with result.open("rb") as pdf:
            if pdf.read(5) != b"%PDF-":
                raise RuntimeError("LibreOffice did not produce a valid PDF")
        result.chmod(0o600)
        os.replace(result, destination)
    return destination


if __name__ == "__main__":
    try:
        parser = argparse.ArgumentParser(description="Convert DOC/DOCX to PDF using LibreOffice")
        parser.add_argument("source")
        parser.add_argument("destination")
        args = parser.parse_args()
        print(json.dumps({"success": True, "path": str(convert_word_to_pdf(args.source, args.destination))}))
    except Exception as error:
        print(json.dumps({"success": False, "error": str(error)}))
        sys.exit(1)
