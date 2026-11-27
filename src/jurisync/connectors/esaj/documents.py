"""The process's document viewer (pasta digital): its inventory and the PDFs, after the ciência check."""

import re
import urllib.parse

from bs4 import BeautifulSoup

from jurisync import portals
from jurisync.connectors.esaj import common, dashboard, intimacoes, search


def consultation_code(session, portal, number):
    """``(code, detail)``: the process code the first-instance consultation shows, and its detail page when the search
    landed on it (None otherwise)."""
    from jurisync.connectors.esaj import parsers

    adapter = common.adapter_for(portal)
    response, _source = search.consultation_search(session, portal, adapter, number)
    link, detail = response.url, response
    if "/show.do" not in urllib.parse.urlsplit(link).path:
        links = parsers.process_links(response.content, adapter.CONSULTATION)
        if len(links) != 1:
            return None, None
        link, detail = urllib.parse.urljoin(response.url, links[0]), None
    code = urllib.parse.parse_qs(urllib.parse.urlsplit(link).query).get("processo.codigo", [""])[0]
    return (code, detail) if dashboard.PROCESS_CODE.fullmatch(code) else (None, detail)


# The panel links the viewer through the consultation of the instance where the process is.
VIEWER_PATHS = frozenset(f"/{app}/abrirPastaDigitalIntegracao.do" for app in ("cpopg", "cpopg5", "cposg", "cposg5"))


VIEWER_PAGE = "/pastadigital/abrirPastaProcessoDigital.do"


SECOND_INSTANCE_DETAIL = frozenset({"/cposg/show.do", "/cposg5/show.do"})


def second_instance_records(session, portal, number, detail):
    """The viewer-URL answer for a case "em grau de recurso": its first-instance page has no "Visualizar autos", only
    "Consultar em 2ª instância". That link opens the second-instance page, whose button asks
    ``verificarAcessoPastaDigital.do?cdProcesso=`` (the page's hidden ``cdProcesso``) as the page's own script does: the
    answer is the viewer URL; 406 is the court's daily limit on unlinked cases; any other refusal is the
    process-password dialog (the account is not linked to the case in the second instance).
    """
    from jurisync.connectors.esaj import parsers

    adapter = common.adapter_for(portal)
    link = parsers.second_instance_link(detail.content, adapter.CONSULTATION, number)
    if not link:
        raise common.EsajBlocked("No authorized document viewer link in the account dashboard or the process page")
    parts = urllib.parse.urlsplit(urllib.parse.urljoin(detail.url, link))
    page = search.consult(session, portal, f"{parts.path}?{parts.query}")
    application = urllib.parse.urlsplit(page.url).path
    if page.status_code != 200 or application not in SECOND_INSTANCE_DETAIL:
        raise common.EsajBlocked("The second-instance page of this process is unavailable to this account")
    code = parsers.hidden_process_code(page.content)
    if not code or not dashboard.PROCESS_CODE.fullmatch(code):
        raise common.EsajParseError("The second-instance page has no process code")
    answer = common.request(
        session,
        portal,
        "GET",
        application.replace("/show.do", "/verificarAcessoPastaDigital.do"),
        params={"cdProcesso": code},
        accept=(403,),
    )
    if answer.status_code == 406:
        raise common.EsajBlocked(
            "The court's daily limit of document-viewer openings for cases the account is not linked to was reached",
            "access_limit",
        )
    if answer.status_code != 200:
        raise common.EsajBlocked(
            "The process is in the second instance and the portal asks for the process password to show its "
            "records there: the account is not linked to the case in the second instance. The password comes from "
            "the court registry; the connector does not ask for it",
            "password_required",
        )
    return answer


def detail_viewer(session, portal, number, code, detail=None):
    """The document viewer reached the way the detail page's "Visualizar autos" button reaches it.

    The button's ``abrirPastaDigital.do?processo.codigo=`` answers with the viewer's own URL as plain text (with a
    one-time ``ticket``), which the page then opens. That URL is followed only on the portal host, on the viewer path and
    for this process number. A detail page whose button opens only the process-password dialog is ``password_required``.
    """
    from jurisync.connectors.esaj import parsers

    adapter = common.adapter_for(portal)
    config = portals.esaj(portal)
    if detail is None:
        detail = search.consult(session, portal, f"{adapter.CONSULTATION}/show.do", params={"processo.codigo": code})
    if parsers.records_need_password(detail.content):
        raise common.EsajBlocked(
            "The portal asks for the process password to show its records: the account is not linked to this "
            "process in e-SAJ. The password comes from the court registry; the connector does not ask for it",
            "password_required",
        )
    link = parsers.records_link(detail.content, adapter.CONSULTATION)
    if link:
        parts = urllib.parse.urlsplit(urllib.parse.urljoin(detail.url, link))
        if urllib.parse.parse_qs(parts.query).get("processo.codigo") != [code]:
            raise common.EsajParseError("The process page links the records of another process")
        answer = common.request(session, portal, "GET", f"{parts.path}?{parts.query}")
    else:
        try:
            answer = second_instance_records(session, portal, number, detail)
        except common.EsajBlocked as refusal:
            if refusal.reason == "access_limit":
                raise
            # A case in the second instance keeps its first-instance records where the account is linked: the page
            # hides their button while the case is away, but the same route still serves them to a linked account
            # (seen live on TJSP). Whatever the portal answers decides; a refusal there keeps the first one.
            answer = common.request(
                session,
                portal,
                "GET",
                f"{adapter.CONSULTATION}/abrirPastaDigital.do",
                params={"processo.codigo": code},
                accept=(403,),
            )
            if answer.status_code != 200 or not (answer.text or "").strip().startswith("https://"):
                raise refusal from None
    target = (answer.text or "").strip() if answer.status_code == 200 else ""
    if not target.startswith("https://") or any(character.isspace() for character in target):
        raise common.EsajBlocked("Document viewer is unavailable to this account")
    common.require_portal_url(config, target, "e-SAJ document viewer")
    viewer = urllib.parse.urlsplit(target)
    numbers = urllib.parse.parse_qs(viewer.query).get("nuProcesso", [])
    if viewer.path != VIEWER_PAGE or [re.sub(r"\D", "", value) for value in numbers] != [re.sub(r"\D", "", number)]:
        raise common.EsajParseError("Unexpected document viewer endpoint")
    response = common.request(session, portal, "GET", f"{viewer.path}?{viewer.query}")
    if response.status_code != 200 or not urllib.parse.urlsplit(response.url).path.startswith("/pastadigital/"):
        raise common.EsajBlocked("Document viewer is unavailable to this account")
    return response


def document_inventory(session, portal, number):
    from jurisync.connectors.esaj import parsers

    number = common.process_number(number, portal)
    item = dashboard.dashboard_process(session, portal, number, petitions=False)
    if not item or not item.get("urlPasta"):
        # The consultation names the process code, which the panel resolves for the cases on the account's panel.
        # A case the panel does not list opens through its detail page's "Visualizar autos", as in the browser.
        # Without a code (consultation behind a CAPTCHA), the petitions the panel lists may still name one.
        try:
            code, detail = consultation_code(session, portal, number)
        except common.EsajBlocked as error:
            if error.reason == "rate_limited":
                raise
            code, detail = None, None
        item = dashboard.dashboard_process(session, portal, number, code=code, petitions=code is None)
        if (not item or not item.get("urlPasta")) and code:
            return parsers.viewer_documents(detail_viewer(session, portal, number, code, detail).text, number)
    if not item or not item.get("urlPasta"):
        raise common.EsajBlocked("No authorized document viewer link in the account dashboard")
    link = item["urlPasta"]
    common.require_portal_url(portals.esaj(portal), link)
    parts = urllib.parse.urlsplit(link)
    if parts.path not in VIEWER_PATHS:
        raise common.EsajParseError("Unexpected document viewer endpoint")
    response = common.request(session, portal, "GET", parts.path + "?" + parts.query)
    if response.status_code != 200 or not urllib.parse.urlsplit(response.url).path.startswith("/pastadigital/"):
        raise common.EsajBlocked("Document viewer is unavailable to this account")
    return parsers.viewer_documents(response.text, number)


def list_documents(session, portal, number, *, acknowledge_ciencia=False):
    read = intimacoes.check_ciencia(session, portal, number, acknowledge_ciencia)
    inventory = document_inventory(session, portal, number)
    return {
        "ciencia": read,
        "processNumber": number,
        "items": [{k: v for k, v in item.items() if k != "parameters"} for item in inventory],
    }


def permission_refusal(response):
    """Why the "intimação não recebida" check does not clear a document, or None when it does.

    The portal answers 204 (or an empty 200) for a document that opens without a receipt. Any body means the page wants
    something first (in the browser: the dialog that confirms receiving the intimação), so it is never treated as a yes.
    """
    body = getattr(response, "content", b"") or b""
    if isinstance(body, str):
        body = body.encode("utf-8", "replace")
    if response.status_code == 204 or (response.status_code == 200 and not body.strip()):
        return None
    from jurisync.connectors.esaj.parsers import redact_text

    text = " ".join(BeautifulSoup(body[:20000], "html.parser").get_text(" ", strip=True).split())
    return f"HTTP {response.status_code}" + (f": {redact_text(text)[:200]}" if text else "")


def document(session, portal, number, document_id, *, download=False, acknowledge_ciencia=False, read=None):
    """One PDF of the process and the ``ciencia`` block of opening it. ``read`` is the block of this call's earlier
    check (a read repeated after a new login: what the first try opened may no longer be pending)."""
    from jurisync.connectors.esaj.parsers import redact_text
    from jurisync.core.document_text import extract_document_text
    from jurisync.core.documents import MAX_DOCUMENT_BYTES, read_limited, validate_document_ids
    from jurisync.core.documents_dir import save_document

    validate_document_ids([document_id])
    if read is None:
        read = intimacoes.check_ciencia(session, portal, number, acknowledge_ciencia)
    inventory = document_inventory(session, portal, number)
    entry = next((item for item in inventory if item["documentId"] == document_id), None)
    if entry is None:
        raise ValueError(
            "documentId is not in the current process inventory: use the ids esaj_list_documents returns (a "
            "movement's documentId from esaj_get_process is the movement link's code, which the viewer does not use)"
        )
    if not entry.get("parameters"):
        raise common.EsajBlocked("Document requires a password or restricted access flow", "password_required")
    # This POST only checks permission. When the portal itself wants the receipt confirmed first (its own dialog, not
    # mapped yet), nothing but that step opens the document, so anything but a clean "yes" stops before the PDF.
    permission = common.request(
        session,
        portal,
        "POST",
        "pastadigital/validarAberturaIntimacaoNaoRecebida.do",
        data={"parametros": entry["parameters"]},
    )
    refusal = permission_refusal(permission)
    if refusal:
        raise common.EsajBlocked(
            "The portal itself asks to confirm the receipt of an intimação before it serves this document "
            f"(it answered {refusal}); nothing was opened. This connector does not have that step yet and "
            "acknowledgeCiencia does not replace it: confirm the receipt in the portal, then read it here.",
            "receipt_required",
        )
    response = common.request(session, portal, "GET", "pastadigital/getPDF.do?" + entry["parameters"], stream=True)
    try:
        content = read_limited(response)
        if content is None:
            raise ValueError(f"Document exceeds {MAX_DOCUMENT_BYTES} bytes")
        if response.status_code != 200 or not content.startswith(b"%PDF-"):
            raise common.EsajBlocked("The document endpoint did not return an authorized PDF")
    finally:
        response.close()
    result = {
        "processNumber": number,
        "documentId": document_id,
        "contentType": "application/pdf",
        "bytes": len(content),
        "ciencia": read,
    }
    if download:
        import uuid

        result["savedAs"] = save_document(f"esaj-{uuid.uuid4().hex}.pdf", content)
    else:
        status, text = extract_document_text(content, "application/pdf", number)
        result.update(extractionStatus=status, text=redact_text(text))
    return result
