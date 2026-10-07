"""JSF/RichFaces mechanics shared by every PJe page interaction.

PJe pages are JSF forms: each response carries a ``javax.faces.ViewState``
that the next request must echo, and actions are RichFaces (A4J) Ajax
submissions identified by generated component ids. Everything here works on
HTML and payloads; requests go through ``post``/``get`` so every call checks
that the portal answered on the expected page with a live session.
"""

import re
from html import unescape
from urllib.parse import urlencode

from bs4 import BeautifulSoup

from jurisync.connectors.pje.parsers import jsf_data, response_html
from jurisync.core.http import IMPERSONATE, TIMEOUT, log_request, same_endpoint

NO_SELECTION = "org.jboss.seam.ui.NoSelectionConverter.noSelectionValue"
LOGIN_MARKERS = ('id="loginform"', "id='loginform'", "/pje/login.seam")
PARTIAL_UPDATE = re.compile(r"<update\b[^>]*\bid=[\"']([^\"']+)[\"'][^>]*>(.*?)</update>", re.I | re.S)


# -- page state ----------------------------------------------------------------


def page_state(html, label="PJe response"):
    """(ViewState, submittable fields) of a page; a missing ViewState means no live form."""
    state, fields = jsf_data(html)
    if not state:
        raise RuntimeError(f"{label} has no current JSF ViewState")
    return state, fields


def view_state(html, label="PJe response"):
    return page_state(html, label)[0]


def form_fields(html, form_id):
    """The form's submittable fields, minus buttons and JS-only link helpers."""
    form = BeautifulSoup(html, "html.parser").find("form", id=form_id)
    if form is None:
        raise RuntimeError(f"PJe form {form_id} was not rendered")
    _, fields = jsf_data(str(form))
    for button in form.find_all("input", attrs={"type": re.compile(r"^(button|submit|reset|image)$", re.I)}):
        fields.pop(button.get("name"), None)
    for name in list(fields):
        if name.endswith((":j_idcl", ":_link_hidden_")):
            fields.pop(name)
    fields[form_id] = form_id
    fields["autoScroll"] = ""
    return fields


def field(soup, suffix, tag="input"):
    """The single element whose generated name or id ends with ``suffix``."""
    found = [
        element
        for element in soup.find_all(tag)
        if (element.get("name") or "").endswith(suffix) or (element.get("id") or "").endswith(suffix)
    ]
    if len(found) != 1:
        raise RuntimeError(f"Expected one PJe field for {suffix}, got {len(found)}")
    return found[0]


def select_option(soup, suffix, label):
    """Return (select, option value, option count) for an exact visible label."""
    selects = [select for select in soup.find_all("select") if (select.get("name") or "").endswith(suffix)]
    if len(selects) != 1:
        raise RuntimeError(f"Expected one PJe select for {suffix}, got {len(selects)}")
    select = selects[0]
    options = [option for option in select.find_all("option") if option.get_text(" ", strip=True) == label]
    if len(options) != 1 or options[0].get("value") in (None, "", NO_SELECTION):
        raise RuntimeError(f"PJe option {label!r} is missing or ambiguous")
    return select, options[0]["value"], len(select.find_all("option")) - 1


# -- A4J actions ---------------------------------------------------------------


def ajax_event(element):
    """(container id, request parameters) of an element's onchange/onclick A4J submit."""
    javascript = element.get("onchange", "") or element.get("onclick", "")
    control = re.search(r"'similarityGroupingId':'([^']+)'", javascript)
    if not control:
        raise RuntimeError("PJe did not render the selection's Ajax action")
    container = re.search(r"'containerId':'([^']+)'", javascript)
    parameters = re.search(r"'parameters':\{([^}]*)\}", javascript)
    values = dict(re.findall(r"'([^']+)':'([^']*)'", parameters.group(1))) if parameters else {}
    values.setdefault(control.group(1), control.group(1))
    return container.group(1) if container else "_viewRoot", values


def ajax_ids(element, form_id="fPP"):
    """Component ids an element's JSF/A4J handlers submit (used to discover controls)."""
    javascript = " ".join(element.get(name, "") for name in ("onclick", "onchange", "onsubmit"))
    prefix = re.escape(form_id)
    ids = re.findall(
        rf"(?:similarityGroupingId|ajaxSingle|control|id)['\"]?\s*[:=]\s*['\"]({prefix}:[^'\"]+)", javascript
    )
    ids += re.findall(rf"A4J\.AJAX\.Submit\(\s*['\"]{prefix}['\"]\s*,.*?['\"]({prefix}:[^'\"]+)['\"]", javascript)
    for invocation in re.findall(r"jsfcljs\([^,]+,\s*\{([^}]*)\}", javascript, re.I):
        action = re.search(rf"['\"]({prefix}:[^'\"]+)['\"]\s*:\s*['\"]\1['\"]", invocation)
        if action:
            ids.append(action.group(1))
    return list(dict.fromkeys(ids))


# -- partial responses ---------------------------------------------------------


def _text(payload):
    return payload.decode("utf-8", errors="replace") if isinstance(payload, bytes) else str(payload or "")


def parse_partial_response(payload):
    """JSF partial-response updates by component id, plus the refreshed ViewState."""
    updates = {}
    for match in PARTIAL_UPDATE.finditer(_text(payload)):
        content = match.group(2).strip()
        if content.startswith("<![CDATA[") and content.endswith("]]>"):
            content = content[9:-3]
        updates[match.group(1)] = unescape(content)
    state = None
    for key, value in updates.items():
        if key.endswith("ViewState"):
            element = BeautifulSoup(value, "html.parser").find("input", attrs={"name": "javax.faces.ViewState"})
            state = element.get("value", "") if element else value.strip()
            break
    return {"updates": updates, "view_state": state}


def apply_partial_updates(form_html, payload):
    """Apply partial-response fragments to the last full form so the next request has live state."""
    parsed = parse_partial_response(payload)
    if not parsed["updates"]:
        return _text(payload)
    soup = BeautifulSoup(form_html, "html.parser")
    for update_id, content in parsed["updates"].items():
        if update_id.endswith("ViewState"):
            for element in soup.find_all("input", attrs={"name": "javax.faces.ViewState"}):
                element["value"] = parsed["view_state"] or content.strip()
            continue
        target = soup.find(id=update_id)
        if not target:
            continue
        fragment = BeautifulSoup(content, "html.parser")
        roots = list(fragment.body.contents if fragment.body else fragment.contents)
        if len(roots) == 1 and getattr(roots[0], "get", lambda *_: None)("id") == update_id:
            target.replace_with(roots[0])
            continue
        target.clear()
        for root in roots:
            target.append(root)
    return str(soup)


# -- requests ------------------------------------------------------------------


def check_page(response, expected_url, label, *, require_state=False):
    """Fail unless the portal answered 200 on the expected page with a live session."""
    location = (getattr(response, "headers", {}) or {}).get("Location", "")
    if 300 <= response.status_code < 400 or location:
        raise RuntimeError(f"{label} redirected; session may have expired")
    if not same_endpoint(getattr(response, "url", expected_url) or expected_url, expected_url):
        raise RuntimeError(f"{label} returned an unexpected URL")
    body = response_html(response)
    if any(marker in body.lower() for marker in LOGIN_MARKERS):
        raise RuntimeError(f"{label} indicates an expired session")
    if response.status_code != 200:
        raise RuntimeError(f"{label} returned HTTP {response.status_code}")
    if require_state and "javax.faces.ViewState" not in body:
        raise RuntimeError(f"{label} lacks JSF ViewState; session may have expired")
    return response


def get(session, url, *, label, referer=None):
    response = session.get(
        url,
        headers={"Referer": referer} if referer else {},
        impersonate=IMPERSONATE,
        allow_redirects=False,
        timeout=TIMEOUT,
    )
    log_request("GET", url, response.status_code)
    return check_page(response, url, label)


def post(session, url, data, *, label, require_state=False):
    """Form POST back to the same page, as the browser's A4J submit does."""
    response = session.post(
        url,
        data=urlencode(data),
        headers={"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8", "Referer": url},
        impersonate=IMPERSONATE,
        allow_redirects=False,
        timeout=TIMEOUT,
    )
    log_request("POST", url, response.status_code)
    return check_page(response, url, label, require_state=require_state)
