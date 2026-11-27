"""JSF/RichFaces mechanics shared by every PJe page interaction.

PJe pages are JSF forms: each response carries a ``javax.faces.ViewState``
that the next request must echo, and actions are RichFaces (A4J) Ajax
submissions identified by generated component ids. Everything here works on
HTML and payloads; requests go through ``post``/``get`` so every call checks
that the portal answered on the expected page with a live session.
"""

import re
from urllib.parse import urlencode

from bs4 import BeautifulSoup

from jurisync.connectors.pje.parsers import jsf_data, response_html
from jurisync.core.http import IMPERSONATE, TIMEOUT, log_request, same_endpoint
from jurisync.core.session_pool import SessionExpired

NO_SELECTION = "org.jboss.seam.ui.NoSelectionConverter.noSelectionValue"
LOGIN_MARKERS = ('id="loginform"', "id='loginform'", "/pje/login.seam")
LOGIN_LOCATION = re.compile(r"login|/auth/realms/", re.I)  # PJe's own login.seam or the national SSO


# -- A4J submits ---------------------------------------------------------------


def a4j_submit(javascript, default_container=None):
    """(form id, container id, parameters) of the ``A4J.AJAX.Submit`` call in an attribute."""
    javascript = re.sub(r"\\x([0-9A-Fa-f]{2})", lambda m: chr(int(m.group(1), 16)), javascript)
    call = re.search(r"A4J\.AJAX\.Submit\(\s*'([^']+)'", javascript)
    if not call:
        raise RuntimeError("PJe did not render the panel's Ajax action")
    container = re.search(r"'containerId':'([^']+)'", javascript)
    parameters = re.search(r"'parameters':\{([^}]*)\}", javascript)
    values = dict(re.findall(r"'([^']+)':'([^']*)'", parameters.group(1))) if parameters else {}
    return call.group(1), container.group(1) if container else default_container, values


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


# -- requests ------------------------------------------------------------------


def check_page(response, expected_url, label, *, require_state=False):
    """Fail unless the portal answered 200 on the expected page with a live session.

    A session sent back to the login page raises ``SessionExpired``, which the session pool answers with one new login.
    """
    location = (getattr(response, "headers", {}) or {}).get("Location", "")
    if 300 <= response.status_code < 400 or location:
        if LOGIN_LOCATION.search(location):
            raise SessionExpired(f"{label} redirected to the login page; the session expired")
        raise RuntimeError(f"{label} redirected; session may have expired")
    if not same_endpoint(getattr(response, "url", expected_url) or expected_url, expected_url):
        raise RuntimeError(f"{label} returned an unexpected URL")
    body = response_html(response)
    if any(marker in body.lower() for marker in LOGIN_MARKERS):
        raise SessionExpired(f"{label} indicates an expired session")
    if response.status_code != 200:
        raise RuntimeError(f"{label} returned HTTP {response.status_code}")
    if require_state and "javax.faces.ViewState" not in body:
        raise RuntimeError(f"{label} lacks JSF ViewState; session may have expired")
    return response


def get(session, url, *, label, referer=None, timeout=TIMEOUT):
    response = session.get(
        url,
        headers={"Referer": referer} if referer else {},
        impersonate=IMPERSONATE,
        allow_redirects=False,
        timeout=timeout,
    )
    log_request("GET", url, response.status_code)
    return check_page(response, url, label)


def post(session, url, data, *, label, require_state=False, timeout=TIMEOUT):
    """Form POST back to the same page, as the browser's A4J submit does."""
    response = session.post(
        url,
        data=urlencode(data),
        headers={"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8", "Referer": url},
        impersonate=IMPERSONATE,
        allow_redirects=False,
        timeout=timeout,
    )
    log_request("POST", url, response.status_code)
    return check_page(response, url, label, require_state=require_state)
