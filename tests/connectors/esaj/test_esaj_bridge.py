from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from jurisync.connectors.esaj import base, bridge, parsers

NUMBER = "0000001-00.2026.8.12.0001"
HTML = f"<html><body><div id='numeroProcesso'>{NUMBER}</div><table id='tabelaTodasMovimentacoes'></table></body></html>"


def page(url="https://esaj.tjms.jus.br/cpopg5/show.do"):
    return SimpleNamespace(content=HTML.encode(), text=HTML, status_code=200, url=url, headers={})


def test_bridge_off_never_touches_a_browser(monkeypatch):
    monkeypatch.setenv("ESAJ_BRIDGE_MODE", "off")
    instance = bridge.BrowserBridge()
    assert not instance.enabled
    with pytest.raises(bridge.BrowserBridgeError):
        instance.search("tjms-esaj", {})
    status = bridge.bridge_status()
    assert status == {"mode": "off", "available": False, "reason": "captcha_required"}


def test_bridge_refuses_foreign_hosts_and_endpoints():
    instance = bridge.BrowserBridge(mode="attach")
    with pytest.raises(bridge.BrowserBridgeError):
        instance.fetch("tjms-esaj", "https://evil.invalid/cpopg5/show.do")
    with pytest.raises(bridge.BrowserBridgeError):
        instance.fetch("tjms-esaj", "https://esaj.tjms.jus.br/tarefas-adv/api/processos")
    with pytest.raises(bridge.BrowserBridgeError):
        bridge.BrowserBridge(mode="stealth")


def test_consultation_search_uses_the_bridge_when_http_blocked(monkeypatch):
    adapter = SimpleNamespace(CONSULTATION="cpopg5")

    def blocked(*_args, **_kwargs):
        raise base.EsajBlocked("Consultation requires CAPTCHA", "captcha_required")

    monkeypatch.setattr(base, "request", blocked)
    monkeypatch.setattr(
        base, "bridge_search", lambda *_args: base.BridgeResponse("https://esaj.tjms.jus.br/cpopg5/search.do", HTML)
    )
    response, source = base.consultation_search(Mock(), "tjms-esaj", adapter, NUMBER)
    assert source == "browser_bridge"
    assert response.status_code == 200


def test_detail_blocked_by_captcha_goes_through_the_bridge(monkeypatch):
    captcha_page = '<html><body><div class="g-recaptcha" data-sitekey="fixture"></div></body></html>'
    monkeypatch.setattr(base, "dashboard_process", lambda *_args: {"urlCpo": "https://esaj.tjms.jus.br/cpopg5/show.do"})
    monkeypatch.setattr(
        base,
        "request",
        lambda *_args, **_kwargs: SimpleNamespace(
            content=captcha_page.encode(),
            text=captcha_page,
            status_code=200,
            url="https://esaj.tjms.jus.br/cpopg5/show.do",
            headers={},
        ),
    )
    monkeypatch.setattr(
        base,
        "bridge_page",
        lambda *_args: base.BridgeResponse("https://esaj.tjms.jus.br/cpopg5/show.do", HTML),
    )
    result = base.load_process(Mock(), "tjms-esaj", NUMBER)
    assert result["consultationSource"] == "browser_bridge"


def test_hidden_token_input_is_not_a_captcha():
    assert not parsers.captcha('<input id="id_recaptcha_response_token" name="recaptcha_response_token" value="">')
    assert not parsers.captcha('<script src="/recaptcha/api.js"></script>')
    assert parsers.captcha('<div class="g-recaptcha" data-sitekey="fixture"></div>')
    assert parsers.captcha("<p>Informe o CAPTCHA para continuar</p>")


def test_search_script_carries_the_page_token_and_form_fields():
    script = bridge.BrowserBridge._script("cpopg5", {"cbPesquisa": "NUMPROC", "cdForo": "-1"})
    assert "#id_recaptcha_response_token" in script
    assert "#uuidCaptcha" in script
    assert "recaptcha_response_token: token" in script
    assert "credentials: 'include'" in script
    assert "/cpopg5/search.do" in script


def test_launch_command_is_headless_and_has_no_stealth_flags(monkeypatch):
    monkeypatch.setattr(
        bridge.shutil, "which", lambda name: "/usr/bin/google-chrome" if name == "google-chrome" else None
    )
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setenv("ESAJ_BRIDGE_PROFILE", "/tmp/opencode/esaj-profile-launch")
    command = bridge.BrowserBridge(mode="launch").chrome_command()
    assert "--headless=new" in command
    assert not any("blink-features" in flag or "automation" in flag.lower() for flag in command)
    assert any(flag.startswith("--user-data-dir=/tmp/opencode/esaj-profile-launch") for flag in command)

    monkeypatch.setenv("ESAJ_BRIDGE_HEADLESS", "false")
    with pytest.raises(bridge.BrowserBridgeError):
        bridge.BrowserBridge(mode="launch").chrome_command()


def test_document_without_parameters_reports_password_required(monkeypatch):
    monkeypatch.setattr(base, "document_inventory", lambda *_args: [{"documentId": "38"}])
    with pytest.raises(base.EsajBlocked) as error:
        base.document(None, "tjsp-esaj", NUMBER, "38")
    assert error.value.reason == "password_required"


def test_intimacoes_app_requires_its_own_sign_in(monkeypatch):
    def blocked(*_args, **_kwargs):
        raise base.EsajBlocked("e-SAJ refused this page (403)", "flow_required")

    monkeypatch.setattr(base, "request", blocked)
    with pytest.raises(base.EsajBlocked) as error:
        base.list_intimacoes(Mock(), "tjms-esaj")
    assert error.value.reason == "login_required"


def test_recaptcha_token_exchange_over_plain_http():
    from jurisync.connectors.esaj import recaptcha

    html = (
        '<script src="https://www.google.com/recaptcha/api.js?render=6Ld7VI4aAAAAAFv5gK5ePhA9j1sXMdAjV3_WX4Gg"></script>'
        "<script>grecaptcha.execute('6Ld7VI4aAAAAAFv5gK5ePhA9j1sXMdAjV3_WX4Gg', {action: 'consulta'})</script>"
    )
    assert recaptcha.sitekey_and_action(html) == ("6Ld7VI4aAAAAAFv5gK5ePhA9j1sXMdAjV3_WX4Gg", "consulta")
    assert recaptcha.encode_origin("https://esaj.tjms.jus.br").endswith("..")
    assert "=" not in recaptcha.encode_origin("https://esaj.tjms.jus.br")
    body = recaptcha.encode_reload_body(v="v1", c="c1", k="k1", action="consulta")
    assert isinstance(body, bytes) and b"consulta" in body
    assert recaptcha.parse_reload_response(')]}\'\n["rresp","TOKEN123",null,60]') == "TOKEN123"
    assert recaptcha.parse_reload_response("garbage") is None
    with __import__("pytest").raises(recaptcha.RecaptchaError):
        recaptcha.sitekey_and_action("<html></html>")


def test_token_page_appends_minted_token(monkeypatch):
    from types import SimpleNamespace

    from jurisync.connectors.esaj import recaptcha

    seen = {}

    def fake_request(session, portal, method, path, **kwargs):
        seen.setdefault("calls", []).append(path)
        if path.endswith("/open.do"):
            return SimpleNamespace(
                content=b"<html></html>",
                text=(
                    "<script>grecaptcha.execute('6Ld7VI4aAAAAAFv5gK5ePhA9j1sXMdAjV3_WX4Gg',"
                    " {action: 'consulta'})</script>"
                ),
                status_code=200,
                url="https://esaj.tjms.jus.br/cpopg5/open.do",
                headers={},
            )
        seen["query"] = path
        return SimpleNamespace(content=b"<html></html>", text="", status_code=200, url="", headers={})

    class FakePosted:
        def json(self):
            return {"uuidCaptcha": "sajcaptcha_fixture"}

    monkeypatch.setattr(base, "request", fake_request)
    monkeypatch.setattr(recaptcha, "mint_token", lambda *a, **k: "TOKEN123")
    session = SimpleNamespace(post=lambda *a, **k: FakePosted())
    base.token_page(session, "tjms-esaj", "https://esaj.tjms.jus.br/cpopg5/show.do?processo.codigo=X")
    assert "recaptcha_response_token=TOKEN123" in seen["query"].replace("%3D", "=") or "TOKEN123" in seen["query"]
    assert "uuidCaptcha=sajcaptcha_fixture" in seen["query"]
