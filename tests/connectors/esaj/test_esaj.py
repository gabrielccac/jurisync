import base64
import hashlib
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa, utils
from mcp.server.mcpserver.exceptions import ToolError

from jurisync import portals
from jurisync.connectors.esaj import base, cas, common, dashboard, documents, intimacoes, parsers, search, server
from jurisync.core.http import UnsafeUrlError

NUMBER = "0000001-00.2026.8.26.0001"
CLEAR = {"estado": "nenhuma"}


def response(body="", status=200, url="https://esaj.tjsp.jus.br/cpopg/show.do"):
    return SimpleNamespace(content=body.encode(), text=body, status_code=status, url=url, headers={})


def test_challenge_signature_and_validation():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    digest = hashlib.sha256(b"fixture").digest()
    challenge = base64.b64encode(digest).decode()
    signature = base.sign_challenge(key, challenge)
    key.public_key().verify(base64.b64decode(signature), digest, padding.PKCS1v15(), utils.Prehashed(hashes.SHA256()))
    assert base.challenge_from(f"var hashDesafio = '{challenge}'") == challenge
    for invalid in ("%%%", "YQ=="):
        with pytest.raises(base.EsajParseError):
            base.sign_challenge(key, invalid)


def test_form_only_browser_fields_and_host_guard():
    html = (
        '<form id="formCertificado" action="/sajcas/login;jsessionid=secret">'
        + "".join(f'<input name="{name}" value="fixture">' for name in base.FORM_FIELDS)
        + '<input name="magistrado" type="checkbox"><button name="button">Login</button></form>'
    )
    config = portals.esaj("tjsp-esaj")
    action, fields = base.login_form(html, config.login_url, config)
    assert action == config.login_url
    assert set(fields) == set(base.FORM_FIELDS)
    with pytest.raises(UnsafeUrlError):
        base.login_form(
            html.replace("/sajcas/login;jsessionid=secret", "https://evil.invalid/login"), config.login_url, config
        )
    with pytest.raises(base.EsajParseError):
        base.login_form("<form/>", config.login_url, config)


def test_request_expiry_403_and_redirect_guard():
    session = Mock()
    session.request.return_value = response(status=403)
    with pytest.raises(base.EsajBlocked):
        base.request(session, "tjsp-esaj", "GET", "cpopg/open.do")
    session.request.return_value = response(url="https://esaj.tjsp.jus.br/sajcas/login")
    with pytest.raises(base.EsajSessionExpired):
        base.request(session, "tjsp-esaj", "GET", "cpopg/open.do")
    session.request.return_value = response(status=302)
    session.request.return_value.headers["Location"] = "https://evil.invalid/steal"
    with pytest.raises(UnsafeUrlError):
        base.request(session, "tjsp-esaj", "GET", "cpopg/open.do")
    session.get.assert_not_called()


def test_mailbox_nonempty_empty_and_unrecognized():
    html = '<input name="pag" value="1"><table><tr><td></td><td>Enviada em</td><td>Assunto</td></tr>'
    assert parsers.messages(html + "</table>") == []
    html += '<tr><td><input name="nuSeqMensagem" value="123"></td><td>05/11/2026</td><td>Protocolo</td></tr></table>'
    assert parsers.messages(html) == [{"messageId": "123", "sentAt": "05/11/2026", "subject": "Protocolo"}]
    with pytest.raises(base.EsajParseError):
        parsers.messages("<html>login</html>")


def test_mailbox_subjects_follow_the_header_charset_not_the_conflicting_meta(monkeypatch):
    # Real shape (TJSP, 19/11/2026): ISO-8859-1 bytes, header charset ISO-8859-1, a UTF-8 meta before an iso-8859-1 one.
    html = (
        '<meta http-equiv="Content-Type" content="text/html; charset=UTF-8">'
        '<meta http-equiv="Content-Type" content="text/html; charset=iso-8859-1"/>'
        '<input name="pag" value="1"><table><tr><td></td><td>Enviada</td><td>Assunto</td></tr>'
        '<tr><td><input name="nuSeqMensagem" value="9"></td><td>05/11/2026</td>'
        "<td>Protocolo Eletr\xf4nico e-Saj - Peti\xe7\xe3o Intermedi\xe1ria Protocolada</td></tr></table>"
    )
    raw = html.encode("latin-1")
    page = SimpleNamespace(content=raw, text=raw.decode("latin-1"), status_code=200, url="https://esaj.tjsp.jus.br/x")
    monkeypatch.setattr(common, "request", lambda *args, **kwargs: page)
    items = base.list_messages(None, "tjsp-esaj")["items"]
    assert items[0]["subject"] == "Protocolo Eletrônico e-Saj - Petição Intermediária Protocolada"


def test_json_privacy_and_process_parser():
    value = parsers.public_json(
        {
            "urlPasta": "https://secret",
            "hash": "secret",
            "nested": [{"token": "secret", "title": "read https://secret/path"}],
        }
    )
    assert value == {"nested": [{"title": "read [url]"}]}
    html = f"""<span id="numeroProcesso">{NUMBER}</span><span id="classeProcesso">Classe</span>
    <table id="tablePartesPrincipais"><tr><td>Autor</td><td>Pessoa Exemplo</td></tr></table>
    <table id="tabelaTodasMovimentacoes"><tr><td>05/11</td><td>Movimento
    <a onclick="validarAberturaIntimacaoNaoRecebida(event,this,'ABC',38,'#liberarAutoPorSenha')">Anexo</a></td></tr></table>"""
    result = parsers.process(html, NUMBER)
    assert result["fields"]["classeProcesso"] == "Classe"
    # A link that opens only the password dialog names no document: the 38 is the movement's sequence.
    assert result["movements"][0]["documents"] == [
        {"documentId": None, "sequence": "38", "title": "Anexo", "accessStatus": "flow_required"}
    ]
    with pytest.raises(base.EsajParseError):
        parsers.process("<html>not found</html>", NUMBER)


def test_process_number_and_captcha():
    assert base.process_number(NUMBER, "tjsp-esaj") == NUMBER
    for invalid in ("not-cnj", NUMBER.replace(".26.", ".12.")):
        with pytest.raises(ValueError):
            base.process_number(invalid, "tjsp-esaj")
    assert parsers.captcha('<div class="g-recaptcha" data-sitekey="fixture"></div>')
    assert not parsers.captcha('<script src="recaptcha.js"></script>')


def test_invalid_document_and_pagination_do_not_request(monkeypatch):
    request = Mock()
    monkeypatch.setattr(common, "request", request)
    with pytest.raises(ValueError):
        base.document(None, "tjsp-esaj", NUMBER, "https://evil.invalid")
    with pytest.raises(ValueError):
        base.list_messages(None, "tjsp-esaj", 0)
    request.assert_not_called()


def test_document_inventory_block_does_not_acknowledge(monkeypatch):
    monkeypatch.setattr(documents, "document_inventory", lambda *a: [{"documentId": "38"}])
    with pytest.raises(base.EsajBlocked):
        base.document(None, "tjsp-esaj", NUMBER, "38", read=CLEAR)
    with pytest.raises(ValueError):
        base.document(None, "tjsp-esaj", NUMBER, "99", read=CLEAR)


def test_server_block_and_tenant_session_usage(monkeypatch):
    pool = Mock()
    pool.run.side_effect = base.EsajBlocked(
        "Need captcha token=secret https://private?token=secret", "captcha_required"
    )
    monkeypatch.setattr(server, "sessions", pool)
    # A refusal is a tool error tagged with its reason, never a payload a client could read as success.
    with pytest.raises(ToolError) as error:
        server.call("tjsp-esaj", lambda s: {})
    assert "[captcha_required]" in str(error.value)
    assert "secret" not in str(error.value)
    assert pool.run.call_args.args[0] == "tjsp-esaj"


@pytest.mark.anyio
async def test_tools_and_registration():
    from jurisync import registry

    tools = await server.create_server().list_tools()
    assert {t.name for t in tools} == {
        "esaj_get_session",
        "esaj_list_messages",
        "esaj_list_tasks",
        "esaj_list_processes",
        "esaj_get_process",
        "esaj_list_documents",
        "esaj_read_document",
        "esaj_download_document",
    }
    # The document tools may register a ciência once acknowledged: destructive, as in every portal; nothing else is.
    opening = [t for t in tools if not t.annotations.read_only_hint]
    assert {t.name for t in opening} == {"esaj_list_documents", "esaj_read_document", "esaj_download_document"}
    for tool in opening:
        assert tool.input_schema["properties"]["acknowledgeCiencia"].get("default") is None
        assert "acknowledgeCiencia" not in tool.input_schema.get("required", [])
    assert {t.name for t in tools if t.annotations.destructive_hint} == {t.name for t in opening}
    assert all(t.title for t in tools)
    assert not hasattr(base, "list_intimacoes")
    assert "esaj" in registry.CONNECTORS
    assert portals.esaj("tjsp-esaj").auth == "certificate_challenge"
    assert portals.eproc("tjsp-1g").system == "eproc"


def test_configured_portals_follow_the_login(monkeypatch):
    for name in ("ESAJ_A1_P12", "ESAJ_A1_PIN", "PJE_A1_P12", "PJE_A1_PIN", "TJMS_ESAJ_USERNAME", "TJMS_ESAJ_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    assert server.configured_portals() == ()
    monkeypatch.setenv("TJMS_ESAJ_USERNAME", "12345678901")
    monkeypatch.setenv("TJMS_ESAJ_PASSWORD", "x")
    assert server.configured_portals() == ("tjms-esaj",)
    monkeypatch.setenv("PJE_A1_P12", "/a1.p12")
    assert server.configured_portals() == ()  # the certificate goes first and its PIN is missing
    monkeypatch.setenv("PJE_A1_PIN", "pin")
    assert server.configured_portals() == portals.keys("esaj")


@pytest.mark.anyio
async def test_server_offers_only_the_given_portals():
    tools = {t.name: t for t in await server.create_server(["tjms-esaj"]).list_tools()}
    field = tools["esaj_get_process"].input_schema["properties"]["portal"]
    assert field.get("enum", [field.get("const")]) == ["tjms-esaj"]
    assert field["default"] == "tjms-esaj"


@pytest.mark.anyio
async def test_a_folder_the_viewer_did_not_list_is_an_error_result(monkeypatch):
    """TJAM 23/11: password_required came back as a success-shaped payload and read as "0 documents"."""
    from mcp.client.client import Client

    pool = Mock()
    pool.run.side_effect = base.EsajBlocked("The portal asks for the process password", "password_required")
    monkeypatch.setattr(server, "sessions", pool)
    async with Client(server.create_server(["tjam-esaj"])) as mcp:
        result = await mcp.call_tool("esaj_list_documents", {"portal": "tjam-esaj", "processNumber": NUMBER})
    assert result.is_error
    assert "[password_required]" in result.content[0].text


def test_main_logs_in_only_where_a_login_is_set(monkeypatch):
    built = []
    monkeypatch.setattr(server, "load_env_file", lambda: None)
    monkeypatch.setattr(server, "serve", lambda srv, on_exit: None)
    monkeypatch.setattr(server, "create_server", built.append)
    monkeypatch.setattr(server, "offer_portals", lambda srv, keys: None)
    monkeypatch.setattr(server, "configured_portals", lambda: ("tjal-esaj",))
    monkeypatch.setattr(server.sessions, "_only", None)
    server.main()
    assert built == [("tjal-esaj",)]
    assert server.sessions._only == ("tjal-esaj",)
    monkeypatch.setattr(server, "configured_portals", lambda: ())
    server.main()
    assert built[-1] == portals.keys("esaj")


def test_document_tools_always_report_the_ciencia(monkeypatch):
    pool = Mock()
    monkeypatch.setattr(server, "sessions", pool)
    pool.run.return_value = {"processNumber": NUMBER, "text": "teor", "ciencia": CLEAR}
    assert server.open_document("tjsp-esaj", lambda s: {})["ciencia"] == CLEAR
    for reason, state in (
        ("intimacao_pending", "pendente"),
        ("receipt_required", "pendente"),
        ("ciencia_unchecked", "nenhuma"),
    ):
        pool.run.side_effect = base.EsajBlocked("nothing was opened", reason)
        result = server.open_document("tjsp-esaj", lambda s: {})
        assert result["success"] is False and result["error"] == reason
        assert result["ciencia"]["estado"] == state
        assert "nada foi aberto" in result["ciencia"]["aviso"].lower()
    pool.run.side_effect = base.EsajBlocked("needs the process password", "password_required")
    with pytest.raises(ToolError, match=r"\[password_required\]"):
        server.open_document("tjsp-esaj", lambda s: {})


def test_a_read_repeated_after_a_new_login_keeps_the_first_ciencia_check(monkeypatch):
    checks, reads = [], []

    def check(session, portal, number, acknowledge):
        checks.append(acknowledge)
        return {"estado": "dada"} if len(checks) == 1 else CLEAR

    def document(session, portal, number, document_id, *, download, read):
        reads.append(read)
        return {"ciencia": read}

    monkeypatch.setattr(intimacoes, "check_ciencia", check)
    monkeypatch.setattr(documents, "document", document)
    pool = Mock()
    pool.run.side_effect = lambda key, read: (read(None), read(None))[1]  # the pool repeats after a new login
    monkeypatch.setattr(server, "sessions", pool)
    assert server.read_document("tjsp-esaj", NUMBER, "38", True)["ciencia"]["estado"] == "dada"
    assert checks == [True] and len(reads) == 2


def viewer_html(number=NUMBER, document_id="38", restricted=False):
    import json
    from urllib.parse import urlencode

    params = urlencode({"nuProcesso": "".join(c for c in number if c.isdigit()), "cdDocumento": document_id})
    entry = {
        "data": {"cdDocumento": document_id, "title": "Documento exemplo", "sigiloAbsoluto": restricted},
        "children": [{"data": {"parametros": params, "nuPaginas": 2}}],
    }
    return "var requestScope = " + json.dumps([entry]) + ";"


def test_viewer_inventory_process_binding_and_privacy(monkeypatch):
    items = parsers.viewer_documents(viewer_html(), NUMBER)
    assert items[0]["documentId"] == "38"
    assert items[0]["pageCount"] == 2
    with pytest.raises(base.EsajParseError):
        parsers.viewer_documents(viewer_html(NUMBER.replace("0000001-", "0000002-")), NUMBER)
    assert "parameters" not in parsers.viewer_documents(viewer_html(restricted=True), NUMBER)[0]
    with pytest.raises(base.EsajBlocked):
        parsers.viewer_documents("<html>password</html>", NUMBER)
    assert parsers.viewer_documents("var requestScope = [];", NUMBER) == []  # listed, and empty
    folder = 'var requestScope = [{"data": {"title": "Pasta"}, "children": []}];'
    with pytest.raises(base.EsajParseError):  # listed nodes, none a document: not an empty folder
        parsers.viewer_documents(folder, NUMBER)
    monkeypatch.setattr(documents, "document_inventory", lambda *a: items)
    monkeypatch.setattr(intimacoes, "check_ciencia", lambda *a: CLEAR)
    assert "parameters" not in str(base.list_documents(None, "tjsp-esaj", NUMBER))


def test_dashboard_context_uses_digits(monkeypatch):
    import re

    replies = iter([[], [], [{"nuProcesso": re.sub(r"\D", "", NUMBER), "urlCpo": "fixture"}]])
    seen = []

    def request(*args, **kwargs):
        seen.append((args, kwargs))
        return SimpleNamespace(status_code=200, json=lambda: next(replies))

    monkeypatch.setattr(common, "request", request)
    assert base.dashboard_process(None, "tjsp-esaj", NUMBER)["urlCpo"] == "fixture"
    assert seen[0][1]["params"]["numerosProcesso"] == re.sub(r"\D", "", NUMBER)
    assert seen[1][1]["params"] == {"situacao": "AGUARDANDO_ASSINATURA"}


@pytest.mark.parametrize(
    ("status", "body", "shown"),
    [
        (409, "", "HTTP 409"),
        (409, "<p>Intimação não recebida</p>", "HTTP 409: Intimação não recebida"),
        # A 200 with a body is the page asking for something (the receipt dialog), never a yes.
        (200, "<html><script>x()</script><b>Confirma o recebimento da intimação?</b></html>", "Confirma o recebimento"),
        (200, '{"mensagem": "ato não recebido token=secret https://private/x"}', "ato não recebido"),
        (200, "x" * 5000, "HTTP 200: " + "x" * 200 + " )"),
        (500, "erro", "HTTP 500: erro"),
    ],
)
def test_document_permission_denial_never_fetches_pdf(monkeypatch, status, body, shown):
    monkeypatch.setattr(documents, "document_inventory", lambda *a: [{"documentId": "38", "parameters": "fixture"}])
    request = Mock(return_value=response(body, status=status))
    monkeypatch.setattr(common, "request", request)
    with pytest.raises(base.EsajBlocked) as error:
        base.document(None, "tjsp-esaj", NUMBER, "38", read=CLEAR)
    assert error.value.reason == "receipt_required"
    assert shown.removesuffix(" )") in str(error.value)
    assert "x" * 201 not in str(error.value)
    assert "secret" not in str(error.value) and "private" not in str(error.value)
    assert request.call_count == 1
    assert request.call_args.args[3] == "pastadigital/validarAberturaIntimacaoNaoRecebida.do"


@pytest.mark.parametrize(("status", "body"), [(204, ""), (200, ""), (200, " \r\n ")])
def test_document_permission_cleared_only_by_204_or_an_empty_200(monkeypatch, status, body):
    monkeypatch.setattr(documents, "document_inventory", lambda *a: [{"documentId": "38", "parameters": "fixture"}])
    pdf = Mock(status_code=200, headers={"content-type": "application/pdf"})
    pdf.iter_content.return_value = iter([b"%PDF-fixture"])
    request = Mock(side_effect=[response(body, status=status), pdf])
    monkeypatch.setattr(common, "request", request)
    monkeypatch.setattr("jurisync.core.document_text.extract_document_text", lambda *a: ("extracted", "Example"))
    assert base.document(None, "tjsp-esaj", NUMBER, "38", read=CLEAR)["text"] == "Example"
    assert request.call_count == 2


def test_captcha_uuid_post_follows_redirects_only_on_the_portal():
    config = portals.esaj("tjms-esaj")
    session = Mock()
    redirect = SimpleNamespace(
        status_code=302,
        url=config.url("cpopg5/captchaControleAcesso.do"),
        headers={"Location": "https://evil.invalid/x"},
    )
    session.post.return_value = redirect
    with pytest.raises(UnsafeUrlError):
        base.captcha_uuid(session, config, "cpopg5")
    assert session.post.call_args.kwargs["allow_redirects"] is False
    session.get.assert_not_called()
    session.post.return_value = SimpleNamespace(status_code=200, url="", headers={}, json=lambda: {"uuidCaptcha": "u1"})
    assert base.captcha_uuid(session, config, "cpopg5") == "u1"
    session.post.return_value = SimpleNamespace(status_code=200, url="", headers={}, json=lambda: ["unexpected"])
    assert base.captcha_uuid(session, config, "cpopg5") == ""


def test_document_bounded_pdf_and_close(monkeypatch):
    monkeypatch.setattr(documents, "document_inventory", lambda *a: [{"documentId": "38", "parameters": "fixture"}])
    pdf = Mock(status_code=200, headers={"content-type": "application/pdf"})
    pdf.iter_content.return_value = iter([b"%PDF-fixture"])
    request = Mock(side_effect=[response(status=204), pdf])
    monkeypatch.setattr(common, "request", request)
    monkeypatch.setattr(
        "jurisync.core.document_text.extract_document_text", lambda *a: ("extracted", "Example https://private/url")
    )
    result = base.document(None, "tjsp-esaj", NUMBER, "38", read=CLEAR)
    assert result["text"] == "Example [url]"
    pdf.close.assert_called_once()
    assert request.call_args.kwargs["stream"] is True


def test_document_oversize_is_not_saved(monkeypatch):
    from jurisync.core.documents import MAX_DOCUMENT_BYTES

    monkeypatch.setattr(documents, "document_inventory", lambda *a: [{"documentId": "38", "parameters": "fixture"}])
    pdf = Mock(status_code=200)
    pdf.iter_content.return_value = iter([b"%PDF-" + b"x" * MAX_DOCUMENT_BYTES])
    monkeypatch.setattr(common, "request", Mock(side_effect=[response(status=204), pdf]))
    save = Mock()
    monkeypatch.setattr("jurisync.core.documents_dir.save_document", save)
    with pytest.raises(ValueError, match="exceeds"):
        base.document(None, "tjsp-esaj", NUMBER, "38", download=True, read=CLEAR)
    save.assert_not_called()
    pdf.close.assert_called_once()


def test_write_routes_refused_before_network():
    session = Mock()
    with pytest.raises(ValueError):
        base.request(session, "tjsp-esaj", "POST", "pastadigital/receberIntimacao.do")
    session.request.assert_not_called()


def test_viewer_accepts_formatted_number_and_redacts_plain_tokens():
    import re

    html = viewer_html().replace(re.sub(r"\D", "", NUMBER), NUMBER)
    assert parsers.viewer_documents(html, NUMBER)[0]["documentId"] == "38"
    assert parsers.public_json({"title": "token=secret signature=private"}) == {"title": "[redacted] [redacted]"}
    assert parsers.redact_text("hash=secret cookie=secret csrf=secret") == "[redacted] [redacted] [redacted]"


def test_stream_redirect_host_guard_closes_response():
    session = Mock()
    redirect = response(status=302)
    redirect.close = Mock()
    redirect.headers["Location"] = "https://evil.invalid/private"
    session.request.return_value = redirect
    with pytest.raises(UnsafeUrlError):
        base.request(session, "tjsp-esaj", "GET", "pastadigital/getPDF.do", stream=True)
    redirect.close.assert_called_once()
    session.get.assert_not_called()


def test_direct_search_detail_still_loads_ajax_movements(monkeypatch):
    html = f'<span id="numeroProcesso">{NUMBER}</span>'
    detail = response(html, url="https://esaj.tjsp.jus.br/cpopg/show.do?processo.codigo=fixture")
    mov = response('<tr class="containerMovimentacao"><td>05/11</td><td>Movimento exemplo</td></tr>')
    replies = iter([response("<form></form>"), detail, mov])
    monkeypatch.setattr(dashboard, "dashboard_process", lambda *a, **k: None)
    request = Mock(side_effect=lambda *a, **kw: next(replies))
    monkeypatch.setattr(common, "request", request)
    result = base.load_process(None, "tjsp-esaj", NUMBER)
    assert len(result["movements"]) == 1
    assert request.call_args.args[3] == "cpopg/carregarMovimentacoesAjax.do"


def test_petition_tasks_are_read_only_and_sanitized(monkeypatch):
    reply = SimpleNamespace(
        status_code=200, json=lambda: [{"id": 1, "urlPasta": "https://private", "situacao": "AGUARDANDO_ASSINATURA"}]
    )
    request = Mock(return_value=reply)
    monkeypatch.setattr(common, "request", request)
    result = base.list_tasks(None, "tjms-esaj", "peticoes")
    assert result["items"] == [{"id": 1, "situacao": "AGUARDANDO_ASSINATURA"}]
    assert request.call_args.args[2:] == ("GET", "tarefas-adv/api/peticoes")
    assert request.call_args.kwargs["params"] == {"situacao": "AGUARDANDO_ASSINATURA"}


def test_tjal_is_registered_on_its_own_host_with_the_consultation_of_tjsp():
    config = portals.esaj("tjal-esaj")
    assert (config.host, config.auth, config.justice, config.region) == (
        "www2.tjal.jus.br",
        "certificate_challenge",
        "8",
        "02",
    )
    assert config.login_url == "https://www2.tjal.jus.br/sajcas/login"
    assert base.consultation("tjal-esaj") == "cpopg" and base.consultation("tjsp-esaj") == "cpopg"
    assert base.consultation("tjms-esaj") == "cpopg5"
    assert base.process_number("0000001-00.2026.8.02.0001", "tjal-esaj")
    with pytest.raises(ValueError):
        base.process_number(NUMBER, "tjal-esaj")


def test_an_http_ticket_redirect_on_the_portal_host_is_upgraded_and_nothing_else():
    # TJAL's CAS answers the ticket redirect with http:// on its own host (TLS ends at the balancer).
    config = portals.esaj("tjal-esaj")
    rewrite = base.upgrade_scheme(config)
    assert rewrite("http://www2.tjal.jus.br/tarefas-adv/?ticket=T") == "https://www2.tjal.jus.br/tarefas-adv/?ticket=T"
    assert rewrite("http://evil.invalid/tarefas-adv/") == "http://evil.invalid/tarefas-adv/"
    assert rewrite("http://www2.tjal.jus.br:8080/x") == "http://www2.tjal.jus.br:8080/x"
    session = Mock()
    session.request.return_value = response(status=302, url="https://www2.tjal.jus.br/caixapostal/")
    session.request.return_value.headers["Location"] = "http://www2.tjal.jus.br/sajcas/login?ticket=T"
    session.get.return_value = response(url="https://www2.tjal.jus.br/caixapostal/abrirCaixaPostal.do")
    base.request(session, "tjal-esaj", "GET", "caixapostal/abrirCaixaPostal.do")
    assert session.get.call_args.args[0] == "https://www2.tjal.jus.br/sajcas/login?ticket=T"
    session.request.return_value.headers["Location"] = "http://evil.invalid/steal"
    with pytest.raises(UnsafeUrlError):
        base.request(session, "tjal-esaj", "GET", "caixapostal/abrirCaixaPostal.do")


def test_tjam_esaj_is_registered_on_the_consultasaj_host_and_upgrades_its_http_redirect():
    config = portals.esaj("tjam-esaj")
    assert (config.host, config.auth, config.justice, config.region) == (
        "consultasaj.tjam.jus.br",
        "certificate_challenge",
        "8",
        "04",
    )
    assert config.login_url == "https://consultasaj.tjam.jus.br/sajcas/login"
    assert base.consultation("tjam-esaj") == "cpopg"
    assert base.process_number("0000001-00.2026.8.04.0001", "tjam-esaj")
    rewrite = base.upgrade_scheme(config)
    url = "http://consultasaj.tjam.jus.br/esajperfil/abrirConfirmacaoDados.do"
    assert rewrite(url) == url.replace("http://", "https://", 1)
    assert rewrite("http://evil.invalid/esajperfil/") == "http://evil.invalid/esajperfil/"


def redirect(location, url):
    reply = response(status=302, url=url)
    reply.headers["Location"] = location
    return reply


def test_tjam_login_page_comes_from_the_panel_with_its_own_service_spelling():
    # TJAM's panel names its CAS service with two slashes; a ticket for the standard spelling ends on erroDesconhecido.
    config = portals.esaj("tjam-esaj")
    service = "https%3A%2F%2Fconsultasaj.tjam.jus.br%2Ftarefas-adv%2F%2Fj_spring_cas_security_check"
    login_url = f"https://consultasaj.tjam.jus.br/sajcas/login?service={service}"
    session = Mock()
    session.get.side_effect = [
        redirect(login_url, "https://consultasaj.tjam.jus.br/tarefas-adv/"),
        response(url=login_url),
    ]
    page = base.login_page(session, config)
    assert page.url == login_url
    assert session.get.call_args_list[0].args[0] == "https://consultasaj.tjam.jus.br/tarefas-adv/"
    assert session.get.call_count == 2


def test_login_page_falls_back_to_the_standard_service_when_the_panel_does_not_lead_to_the_cas():
    config = portals.esaj("tjsp-esaj")
    session = Mock()
    session.get.side_effect = [
        response(status=404, url="https://esaj.tjsp.jus.br/tarefas-adv/"),
        response(url="https://esaj.tjsp.jus.br/sajcas/login?service=x"),
    ]
    base.login_page(session, config)
    assert session.get.call_args.args[0] == "https://esaj.tjsp.jus.br/sajcas/login"
    assert session.get.call_args.kwargs["params"] == {"service": config.url(base.SERVICE_PATH)}


def test_rewrite_drops_a_trailing_jsessionid_only_on_the_portal_host():
    rewrite = base.upgrade_scheme(portals.esaj("tjam-esaj"))
    assert (
        rewrite("https://consultasaj.tjam.jus.br/tarefas-adv/erroDesconhecido.do;jsessionid=ABC.n1?x=1")
        == "https://consultasaj.tjam.jus.br/tarefas-adv/erroDesconhecido.do?x=1"
    )
    assert rewrite("https://evil.invalid/a;jsessionid=ABC") == "https://evil.invalid/a;jsessionid=ABC"
    with pytest.raises(UnsafeUrlError):
        base.require_portal_url(portals.esaj("tjam-esaj"), rewrite("https://consultasaj.tjam.jus.br/a;x=1"))


def test_session_check_uses_the_cas_script_where_the_session_api_is_missing(monkeypatch):
    session = Mock()
    session.get.side_effect = [
        response(status=404, url="https://consultasaj.tjam.jus.br/esaj/api/auth/session"),
        response("window.sajcas = { usuarioLogadoNoCasServer: true };", url="https://consultasaj.tjam.jus.br/x"),
    ]
    assert base.refresh("tjam-esaj", session) is session
    assert session.get.call_args.args[0] == "https://consultasaj.tjam.jus.br/sajcas/verificarLogin.js"
    session.get.side_effect = [
        response(status=404),
        response("window.sajcas = { usuarioLogadoNoCasServer: false };"),
    ]
    assert base.refresh("tjam-esaj", session) is None
    monkeypatch.setattr(common, "request", Mock(return_value=response(status=404)))
    session.get.side_effect = [response("window.sajcas = { usuarioLogadoNoCasServer: true };")]
    result = base.get_session(session, "tjam-esaj")
    assert result["usuarioLogado"] is True and result["sessionSource"] == "sajcas"
    session.get.side_effect = [response("window.sajcas = { usuarioLogadoNoCasServer: false };")]
    with pytest.raises(base.EsajSessionExpired):
        base.get_session(session, "tjam-esaj")


def test_tjam_process_uses_the_cpopg_consultation(monkeypatch):
    number = "0000001-00.2026.8.04.0001"
    detail = response(
        f'<span id="numeroProcesso">{number}</span><span id="classeProcesso">Classe</span>',
        url="https://consultasaj.tjam.jus.br/cpopg/show.do?processo.codigo=X",
    )
    monkeypatch.setattr(dashboard, "dashboard_process", lambda *a, **k: None)
    monkeypatch.setattr(search, "consultation_search", lambda s, p, adapter, n: (detail, "http"))
    result = base.load_process(None, "tjam-esaj", number)
    assert result["fields"]["classeProcesso"] == "Classe" and result["consultationSource"] == "http"


def test_a_number_the_consultation_does_not_know_is_reported_as_not_found(monkeypatch):
    # TJAM's e-SAJ holds only the old case stock: current numbers answer with the consultation's "no information" page.
    page = response(
        "<div>Não existem informações disponíveis para os parâmetros informados.</div>",
        url="https://consultasaj.tjam.jus.br/cpopg/search.do",
    )
    monkeypatch.setattr(dashboard, "dashboard_process", lambda *a, **k: None)
    monkeypatch.setattr(search, "consultation_search", lambda *a: (page, "http"))
    monkeypatch.setattr(common, "request", lambda *a, **k: page)

    def no_token(*a):
        raise base.EsajBlocked("Consultation requires CAPTCHA", "captcha_required")

    monkeypatch.setattr(search, "token_search", no_token)
    with pytest.raises(base.EsajBlocked) as error:
        base.load_process(None, "tjam-esaj", "0000001-00.2026.8.04.0001")
    assert error.value.reason == "not_found"
    assert not parsers.not_found("<div>Processo 1</div>")


TJAM_LOGIN = "https://consultasaj.tjam.jus.br/sajcas/login?service=x"
PASSWORD_PAGE = (
    '<form id="formCertificado" action="/sajcas/login;jsessionid=s?service=x">'
    + "".join(f'<input type="hidden" name="{name}" value="v-{name}">' for name in base.FORM_FIELDS)
    + '<input type="text" name="username"><input type="password" name="password">'
    + '<input type="submit" name="pbEntrar" value="Entrar"><input type="hidden" name="signature">'
    + '<input type="hidden" name="certificadoSelecionado"><input type="button" name="pbEntrar" value="Entrar"></form>'
    + "<script>$.saj.cas.DuploFatorHabilitado = false || false;</script>"
)


def password_session(after, cookie=None):
    session = Mock()
    session.cookies.jar = [SimpleNamespace(name=cookie)] if cookie else []
    session.post.return_value = response(after, url=TJAM_LOGIN)
    return session


def test_password_form_posts_what_the_login_tab_sends():
    config = portals.esaj("tjam-esaj")
    action, fields = base.password_form(PASSWORD_PAGE, TJAM_LOGIN, config, "000.000.000-00", "secret")
    assert action == "https://consultasaj.tjam.jus.br/sajcas/login?service=x"
    assert fields == {
        **{name: f"v-{name}" for name in base.FORM_FIELDS},
        "username": "000.000.000-00",
        "password": "secret",
        "signature": "",
        "certificadoSelecionado": "",
        "pbEntrar": "Entrar",
    }


@pytest.mark.parametrize(
    ("after", "cookie", "raised"),
    [
        ("<p>ok</p>", "CASTGC", None),
        (PASSWORD_PAGE.replace("false || false", "true || false"), None, base.EsajTwoFactorRequired),
        (
            PASSWORD_PAGE.replace("<form", '<div id="mensagemRetorno">Usuário e/ou senha inválidos</div><form'),
            None,
            None,
        ),
    ],
)
def test_password_login_success_two_factor_and_refusal(monkeypatch, after, cookie, raised):
    monkeypatch.setenv("TJAM_ESAJ_USERNAME", "000.000.000-00")
    monkeypatch.setenv("TJAM_ESAJ_PASSWORD", "secret")
    monkeypatch.setattr(cas.credentials, "has", lambda name: False)
    monkeypatch.setattr(cas, "login_page", lambda s, c: response(PASSWORD_PAGE, url=TJAM_LOGIN))
    monkeypatch.setattr(cas, "load_certificate", lambda **k: pytest.fail("no certificate on a password login"))
    session = password_session(after, cookie)
    if cookie:
        assert base.login("tjam-esaj", session=session) is session
    elif raised:
        with pytest.raises(raised, match="e-mail"):
            base.login("tjam-esaj", session=session)
    else:
        with pytest.raises(cas.LoginRejected, match="inválidos") as error:
            base.login("tjam-esaj", session=session)
        assert not isinstance(error.value, base.EsajTwoFactorRequired)
    assert session.post.call_count == 1
    assert session.post.call_args.kwargs["data"]["password"] == "secret"


def test_the_certificate_wins_when_the_account_has_one(monkeypatch):
    monkeypatch.setenv("TJAM_ESAJ_USERNAME", "000.000.000-00")
    monkeypatch.setenv("TJAM_ESAJ_PASSWORD", "secret")
    monkeypatch.setattr(cas.credentials, "has", lambda name: name == "PJE_A1_P12")
    assert not base.uses_password(portals.esaj("tjam-esaj"))
    monkeypatch.setattr(cas.credentials, "has", lambda name: False)
    assert base.uses_password(portals.esaj("tjam-esaj"))
    monkeypatch.delenv("TJAM_ESAJ_PASSWORD")
    assert not base.uses_password(portals.esaj("tjam-esaj"))


def test_the_last_movements_table_does_not_repeat_the_full_table():
    # TJAM 2026-11-16: the detail page carries both tables; reading both doubled the first five movements.
    row = '<tr class="containerMovimentacao"><td>0{n}/11/2026</td><td></td><td>Movimento {n}</td></tr>'
    last = "".join(row.format(n=n) for n in (3, 2))
    full = "".join(row.format(n=n) for n in (3, 2, 1))
    html = f'<table id="tabelaUltimasMovimentacoes">{last}</table><table id="tabelaTodasMovimentacoes">{full}</table>'
    assert [m["text"] for m in parsers.movements(html)] == ["Movimento 3", "Movimento 2", "Movimento 1"]
    assert len(parsers.movements(f'<table id="tabelaUltimasMovimentacoes">{last}</table>')) == 2
    assert len(parsers.movements(f"<table>{full}</table>")) == 3


def test_a_bare_cpf_is_sent_in_the_mask_the_cas_expects(monkeypatch):
    monkeypatch.setenv("TJMS_ESAJ_USERNAME", "12345678901")
    monkeypatch.setenv("TJMS_ESAJ_PASSWORD", "secret")
    assert base.password_credentials(portals.esaj("tjms-esaj")) == ("123.456.789-01", "secret")
    monkeypatch.setenv("TJMS_ESAJ_USERNAME", "000.000.000-00")
    assert base.password_credentials(portals.esaj("tjms-esaj"))[0] == "000.000.000-00"
