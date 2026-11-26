"""JSF (JavaServer Faces / RichFaces) page mechanics shared by the systems built on it (PJe, JPe): the ViewState and
fields a form posts back, the page text in the charset the server really used, and the parameters of an A4J call."""

import re

from bs4 import BeautifulSoup


def jsf_data(html):
    soup = BeautifulSoup(html, "html.parser")
    state = soup.find("input", attrs={"name": "javax.faces.ViewState"})
    fields = {}
    for element in soup.find_all(["input", "select"]):
        name = element.get("name")
        if not name or name == "javax.faces.ViewState":
            continue
        if element.name == "input" and element.get("type") in {"checkbox", "radio"} and not element.has_attr("checked"):
            continue
        if element.name == "select":
            option = element.find("option", selected=True) or element.find("option")
            fields[name] = option.get("value", "") if option else ""
        else:
            fields[name] = element.get("value", "")
    return (state.get("value") if state else None), fields


def response_html(response):
    content = getattr(response, "content", b"")
    if not isinstance(content, (bytes, bytearray)):
        return response.text
    content_type = response.headers.get("Content-Type", "").lower()
    header_match = re.search(r"charset\s*=\s*[\"']?([a-z0-9_-]+)", content_type, re.I)
    meta_match = re.search(rb"charset\s*=\s*[\"']?([a-z0-9_-]+)", content[:4096], re.I)
    charset = (
        header_match.group(1).lower()
        if header_match
        else meta_match.group(1).decode("ascii", errors="ignore").lower()
        if meta_match
        else ""
    )
    if charset in {"iso-8859-1", "latin1", "latin-1"}:
        return bytes(content).decode("iso-8859-1")
    try:
        return bytes(content).decode("utf-8")
    except UnicodeDecodeError:
        # PJe has served Latin-1 bytes while declaring UTF-8.
        return bytes(content).decode("cp1252")


def a4j_submit(javascript, default_container=None, label="PJe"):
    """(form id, container id, parameters) of the ``A4J.AJAX.Submit`` call in an attribute."""
    javascript = re.sub(r"\\x([0-9A-Fa-f]{2})", lambda m: chr(int(m.group(1), 16)), javascript)
    call = re.search(r"A4J\.AJAX\.Submit\(\s*'([^']+)'", javascript)
    if not call:
        raise RuntimeError(f"{label} did not render the panel's Ajax action")
    container = re.search(r"'containerId':'([^']+)'", javascript)
    parameters = re.search(r"'parameters':\{([^}]*)\}", javascript)
    values = dict(re.findall(r"'([^']+)':'([^']*)'", parameters.group(1))) if parameters else {}
    return call.group(1), container.group(1) if container else default_container, values
