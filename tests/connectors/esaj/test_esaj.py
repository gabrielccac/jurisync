import base64
import hashlib
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa, utils

from jurisync import portals
from jurisync.connectors.esaj import base, parsers, server
from jurisync.core.http import UnsafeUrlError

NUMBER = "0000001-00.2026.8.26.0001"


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
    assert result["movements"][0]["documents"][0]["documentId"] == "38"
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
    monkeypatch.setattr(base, "request", request)
    with pytest.raises(ValueError):
        base.document(None, "tjsp-esaj", NUMBER, "https://evil.invalid")
    with pytest.raises(ValueError):
        base.list_messages(None, "tjsp-esaj", 0)
    request.assert_not_called()


def test_document_inventory_block_does_not_acknowledge(monkeypatch):
    monkeypatch.setattr(base, "document_inventory", lambda *a: [{"documentId": "38"}])
    with pytest.raises(base.EsajBlocked):
        base.document(None, "tjsp-esaj", NUMBER, "38")
    with pytest.raises(ValueError):
        base.document(None, "tjsp-esaj", NUMBER, "99")


def test_server_block_and_tenant_session_usage(monkeypatch):
    pool = Mock()
    pool.run.side_effect = base.EsajBlocked(
        "Need captcha token=secret https://private?token=secret", "captcha_required"
    )
    monkeypatch.setattr(server, "sessions", pool)
    result = server.call("tjsp-esaj", lambda s: {})
    assert result["error"] == "captcha_required"
    assert "secret" not in str(result)
    assert pool.run.call_args.args[0] == "tjsp-esaj"


@pytest.mark.anyio
async def test_tools_and_registration():
    from jurisync.install.serve import SERVERS

    tools = await server.create_server().list_tools()
    assert {t.name for t in tools} == {
        "esaj_get_session",
        "esaj_list_messages",
        "esaj_list_intimacoes",
        "esaj_list_tasks",
        "esaj_get_process",
        "esaj_list_documents",
        "esaj_read_document",
        "esaj_download_document",
    }
    assert all(t.annotations.read_only_hint and not t.annotations.destructive_hint for t in tools)
    assert "esaj" in SERVERS
    assert portals.esaj("tjsp-esaj").auth == "certificate_challenge"
    assert portals.eproc("tjsp-1g").system == "eproc"


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
    monkeypatch.setattr(base, "document_inventory", lambda *a: items)
    assert "parameters" not in str(base.list_documents(None, "tjsp-esaj", NUMBER))


def test_dashboard_context_uses_digits(monkeypatch):
    import re

    replies = iter([[], [], [{"nuProcesso": re.sub(r"\D", "", NUMBER), "urlCpo": "fixture"}]])
    seen = []

    def request(*args, **kwargs):
        seen.append((args, kwargs))
        return SimpleNamespace(status_code=200, json=lambda: next(replies))

    monkeypatch.setattr(base, "request", request)
    assert base.dashboard_process(None, "tjsp-esaj", NUMBER)["urlCpo"] == "fixture"
    assert seen[0][1]["params"]["numerosProcesso"] == re.sub(r"\D", "", NUMBER)
    assert seen[1][1]["params"] == {"situacao": "AGUARDANDO_ASSINATURA"}


def test_document_permission_denial_never_fetches_pdf(monkeypatch):
    monkeypatch.setattr(base, "document_inventory", lambda *a: [{"documentId": "38", "parameters": "fixture"}])
    request = Mock(return_value=response(status=409))
    monkeypatch.setattr(base, "request", request)
    with pytest.raises(base.EsajBlocked):
        base.document(None, "tjsp-esaj", NUMBER, "38")
    assert request.call_count == 1
    assert request.call_args.args[3] == "pastadigital/validarAberturaIntimacaoNaoRecebida.do"


def test_document_bounded_pdf_and_close(monkeypatch):
    monkeypatch.setattr(base, "document_inventory", lambda *a: [{"documentId": "38", "parameters": "fixture"}])
    pdf = Mock(status_code=200, headers={"content-type": "application/pdf"})
    pdf.iter_content.return_value = iter([b"%PDF-fixture"])
    request = Mock(side_effect=[response(status=204), pdf])
    monkeypatch.setattr(base, "request", request)
    monkeypatch.setattr(
        "jurisync.core.document_text.extract_document_text", lambda *a: ("extracted", "Example https://private/url")
    )
    result = base.document(None, "tjsp-esaj", NUMBER, "38")
    assert result["text"] == "Example [url]"
    pdf.close.assert_called_once()
    assert request.call_args.kwargs["stream"] is True


def test_document_oversize_is_not_saved(monkeypatch):
    from jurisync.core.documents import MAX_DOCUMENT_BYTES

    monkeypatch.setattr(base, "document_inventory", lambda *a: [{"documentId": "38", "parameters": "fixture"}])
    pdf = Mock(status_code=200)
    pdf.iter_content.return_value = iter([b"%PDF-" + b"x" * MAX_DOCUMENT_BYTES])
    monkeypatch.setattr(base, "request", Mock(side_effect=[response(status=204), pdf]))
    save = Mock()
    monkeypatch.setattr("jurisync.core.documents_dir.save_document", save)
    with pytest.raises(ValueError, match="exceeds"):
        base.document(None, "tjsp-esaj", NUMBER, "38", download=True)
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
    monkeypatch.setattr(base, "dashboard_process", lambda *a: None)
    request = Mock(side_effect=lambda *a, **kw: next(replies))
    monkeypatch.setattr(base, "request", request)
    result = base.load_process(None, "tjsp-esaj", NUMBER)
    assert len(result["movements"]) == 1
    assert request.call_args.args[3] == "cpopg/carregarMovimentacoesAjax.do"


def test_petition_tasks_are_read_only_and_sanitized(monkeypatch):
    reply = SimpleNamespace(
        status_code=200, json=lambda: [{"id": 1, "urlPasta": "https://private", "situacao": "AGUARDANDO_ASSINATURA"}]
    )
    request = Mock(return_value=reply)
    monkeypatch.setattr(base, "request", request)
    result = base.list_tasks(None, "tjms-esaj", "peticoes")
    assert result["items"] == [{"id": 1, "situacao": "AGUARDANDO_ASSINATURA"}]
    assert request.call_args.args[2:] == ("GET", "tarefas-adv/api/peticoes")
    assert request.call_args.kwargs["params"] == {"situacao": "AGUARDANDO_ASSINATURA"}
