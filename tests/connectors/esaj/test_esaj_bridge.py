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
    monkeypatch.setattr(
        base, "dashboard_process", lambda *_args, **_kwargs: {"urlCpo": "https://esaj.tjms.jus.br/cpopg5/show.do"}
    )
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
    command = bridge.BrowserBridge(mode="launch").chrome_command("/scratch/esaj-profile-launch")
    assert "--headless=new" in command
    assert not any("blink-features" in flag or "automation" in flag.lower() for flag in command)
    assert "--user-data-dir=/scratch/esaj-profile-launch" in command
    assert "--remote-debugging-port=0" in command
    # The websocket is opened without an Origin header, so no origin has to be allowed.
    assert not any("remote-allow-origins" in flag for flag in command)

    monkeypatch.setenv("ESAJ_BRIDGE_HEADLESS", "false")
    with pytest.raises(bridge.BrowserBridgeError):
        bridge.BrowserBridge(mode="launch").chrome_command("/scratch/esaj-profile-launch")


def test_document_without_parameters_reports_password_required(monkeypatch):
    monkeypatch.setattr(base, "document_inventory", lambda *_args: [{"documentId": "38"}])
    with pytest.raises(base.EsajBlocked) as error:
        base.document(None, "tjsp-esaj", NUMBER, "38", read={"estado": "nenhuma"})
    assert error.value.reason == "password_required"


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
        status_code = 200
        headers = {}
        url = ""

        def json(self):
            return {"uuidCaptcha": "sajcaptcha_fixture"}

    monkeypatch.setattr(base, "request", fake_request)
    monkeypatch.setattr(recaptcha, "mint_token", lambda *a, **k: "TOKEN123")
    session = SimpleNamespace(post=lambda *a, **k: FakePosted())
    base.token_page(session, "tjms-esaj", "https://esaj.tjms.jus.br/cpopg5/show.do?processo.codigo=X")
    assert "recaptcha_response_token=TOKEN123" in seen["query"].replace("%3D", "=") or "TOKEN123" in seen["query"]
    assert "uuidCaptcha=sajcaptcha_fixture" in seen["query"]


# --- Browser lifecycle, with the process, the DevTools HTTP endpoint and the websocket faked ------------------------


class FakeChrome:
    """Stands in for ``subprocess.Popen``: records the profile it was given and, like Chrome, writes its port there."""

    started = []

    def __init__(self, command, **_kwargs):
        from pathlib import Path

        self.command = command
        self.profile = Path(next(flag.split("=", 1)[1] for flag in command if flag.startswith("--user-data-dir=")))
        self.terminated = False
        self.port_file = self.profile / "DevToolsActivePort"
        self.port_file.write_text(self.first_write, encoding="utf-8")
        FakeChrome.started.append(self)

    first_write = "41001\n/devtools/browser/x"

    def poll(self):
        return None

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0


class FakeTab:
    """Stands in for ``CdpTarget``: records every DevTools call."""

    opened = []

    def __init__(self, websocket_url, timeout=30):
        self.url = websocket_url
        self.calls = []
        self.closed = False
        FakeTab.opened.append(self)

    def call(self, method, params=None):
        self.calls.append((method, params or {}))
        return {}

    def evaluate(self, expression):
        if "readyState" in expression:
            return "complete"
        return HTML

    set_cookies = bridge.CdpTarget.set_cookies
    delete_cookies = bridge.CdpTarget.delete_cookies

    def close(self):
        self.closed = True


@pytest.fixture
def fake_browser(monkeypatch):
    FakeChrome.started, FakeTab.opened = [], []
    requested = []

    def fake_json(url, method="GET", timeout=5):
        requested.append(url)
        if "/json/new" in url:
            return {"id": "tab1", "webSocketDebuggerUrl": "ws://127.0.0.1/devtools/page/tab1"}
        return {"Browser": "fake"}

    monkeypatch.setattr(bridge, "_json", fake_json)
    monkeypatch.setattr(bridge, "CdpTarget", FakeTab)
    monkeypatch.setattr(bridge.subprocess, "Popen", FakeChrome)
    monkeypatch.setattr(bridge.shutil, "which", lambda name: "/usr/bin/google-chrome")
    monkeypatch.setattr(bridge.time, "sleep", lambda _seconds: None)
    return requested


COOKIES = [{"name": "JSESSIONID", "value": "fixture", "secure": True, "httpOnly": True}]
PAGE = "https://esaj.tjms.jus.br/cpopg5/show.do?processo.codigo=X"


def test_launch_starts_its_own_chrome_on_a_temporary_profile_and_removes_it(fake_browser, monkeypatch, tmp_path):
    from pathlib import Path

    # A Chrome already listening on the configured endpoint is not ours: launch must not attach to it.
    monkeypatch.setenv("ESAJ_BRIDGE_CDP", "http://127.0.0.1:9222")
    monkeypatch.setenv("ESAJ_BRIDGE_PROFILE", str(tmp_path / "operator-profile"))
    with bridge.BrowserBridge(mode="launch") as browser:
        page = browser.fetch("tjms-esaj", PAGE, COOKIES)
        chrome = FakeChrome.started[0]
        assert chrome.profile.is_dir() and chrome.profile != tmp_path / "operator-profile"
        assert Path(chrome.profile).name.startswith("jurisync-esaj-chrome-")
    assert page["html"] == HTML
    assert len(FakeChrome.started) == 1 and chrome.terminated
    assert not chrome.profile.exists()
    assert all(url.startswith("http://127.0.0.1:41001/") for url in fake_browser)
    tab = FakeTab.opened[0]
    assert tab.closed
    assert [params["name"] for method, params in tab.calls if method == "Network.setCookie"] == ["JSESSIONID"]
    # The profile is thrown away, so nothing has to be deleted from it.
    assert not [method for method, _ in tab.calls if method == "Network.deleteCookies"]


def test_each_launch_gets_its_own_profile(fake_browser):
    profiles = []
    for _ in range(2):
        with bridge.BrowserBridge(mode="launch") as browser:
            browser.fetch("tjms-esaj", PAGE, COOKIES)
            profiles.append(FakeChrome.started[-1].profile)
    assert profiles[0] != profiles[1]
    assert not any(profile.exists() for profile in profiles)


def test_attach_reuses_the_operators_chrome_and_deletes_the_injected_cookies(fake_browser, monkeypatch):
    monkeypatch.setenv("ESAJ_BRIDGE_CDP", "http://127.0.0.1:9222")
    for mode in ("attach", "auto"):
        FakeTab.opened.clear()
        with bridge.BrowserBridge(mode=mode) as browser:
            browser.fetch("tjms-esaj", PAGE, COOKIES)
        assert FakeChrome.started == []
        tab = FakeTab.opened[0]
        deleted = [params for method, params in tab.calls if method == "Network.deleteCookies"]
        assert deleted == [{"name": "JSESSIONID", "domain": "esaj.tjms.jus.br", "path": "/"}]
        assert tab.calls[-1][0] == "Network.deleteCookies" and tab.closed
    assert all(url.startswith("http://127.0.0.1:9222/") for url in fake_browser)


def test_cookies_are_deleted_from_an_attached_browser_even_when_the_page_fails(fake_browser, monkeypatch):
    monkeypatch.setattr(FakeTab, "evaluate", lambda self, expression: "loading")
    monkeypatch.setattr(bridge.time, "monotonic", iter(range(0, 10_000, 30)).__next__)
    with bridge.BrowserBridge(mode="attach", timeout=60) as browser, pytest.raises(bridge.BrowserBridgeError):
        browser.fetch("tjms-esaj", PAGE, COOKIES)
    tab = FakeTab.opened[0]
    assert [method for method, _ in tab.calls if method == "Network.deleteCookies"] == ["Network.deleteCookies"]
    assert tab.closed


def test_attach_without_a_browser_never_launches_and_auto_falls_back_to_a_launch(fake_browser, monkeypatch, tmp_path):
    def no_operator_browser(url, method="GET", timeout=5):
        if ":9222" in url:
            raise OSError("connection refused")
        if "/json/new" in url:
            return {"id": "tab1", "webSocketDebuggerUrl": "ws://x"}
        return {"Browser": "fake"}

    monkeypatch.setattr(bridge, "_json", no_operator_browser)
    monkeypatch.setenv("ESAJ_BRIDGE_PROFILE", str(tmp_path / "missing"))
    with bridge.BrowserBridge(mode="attach") as browser, pytest.raises(bridge.BrowserBridgeError, match="reachable"):
        browser.fetch("tjms-esaj", PAGE, COOKIES)
    assert FakeChrome.started == []
    with bridge.BrowserBridge(mode="auto") as browser:
        browser.fetch("tjms-esaj", PAGE, COOKIES)
    assert len(FakeChrome.started) == 1 and not FakeChrome.started[0].profile.exists()


def test_half_written_port_file_is_waited_for_not_an_index_error(fake_browser, monkeypatch, tmp_path):
    for content in ("", "\n", "abc\n", "0\n", "99999999\n"):
        (tmp_path / "DevToolsActivePort").write_text(content, encoding="utf-8")
        assert bridge.recorded_port(tmp_path) is None
    assert bridge.recorded_port(tmp_path / "missing") is None
    (tmp_path / "DevToolsActivePort").write_text("9333\n/devtools/browser/x", encoding="utf-8")
    assert bridge.recorded_port(tmp_path) == "9333"
    # An operator profile with an empty port file is simply "no browser" in attach mode.
    (tmp_path / "DevToolsActivePort").write_text("", encoding="utf-8")
    monkeypatch.setattr(bridge, "_json", Mock(side_effect=OSError("refused")))
    assert bridge.BrowserBridge(mode="attach", profile=str(tmp_path)).endpoint_of_running_browser() is None

    # Chrome creates the file before it writes the port: the launch keeps polling until the port is there.
    monkeypatch.setattr(bridge, "_json", lambda url, method="GET", timeout=5: {"Browser": "fake"})
    monkeypatch.setattr(FakeChrome, "first_write", "")
    polls = []

    def write_port_later(_seconds):
        polls.append(1)
        if len(polls) == 2:
            FakeChrome.started[-1].port_file.write_text("41002\n", encoding="utf-8")

    monkeypatch.setattr(bridge.time, "sleep", write_port_later)
    with bridge.BrowserBridge(mode="launch") as browser:
        assert browser.start() == "http://127.0.0.1:41002"
    assert len(polls) == 2


def test_failed_launch_stops_chrome_and_removes_the_profile(fake_browser, monkeypatch):
    monkeypatch.setattr(FakeChrome, "poll", lambda self: 1)
    monkeypatch.setattr(FakeChrome, "first_write", "")
    browser = bridge.BrowserBridge(mode="launch")
    with pytest.raises(bridge.BrowserBridgeError, match="exited"):
        browser.start()
    chrome = FakeChrome.started[0]
    assert chrome.terminated and not chrome.profile.exists()
    assert not bridge._LAUNCH_LOCK.locked()


def test_launches_do_not_interleave(fake_browser, monkeypatch):
    import threading

    active, overlaps = [], []

    class SlowChrome(FakeChrome):
        def __init__(self, command, **kwargs):
            active.append(1)
            overlaps.append(len(active))
            super().__init__(command, **kwargs)

    def version(self, endpoint):
        active.pop()
        return {"Browser": "fake"}

    monkeypatch.setattr(bridge.subprocess, "Popen", SlowChrome)
    monkeypatch.setattr(bridge.BrowserBridge, "_version", version)

    def run():
        with bridge.BrowserBridge(mode="launch") as browser:
            browser.start()

    threads = [threading.Thread(target=run) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert overlaps == [1, 1, 1, 1]


def test_bridge_status_does_not_look_for_a_running_browser_in_launch_mode(fake_browser, monkeypatch):
    monkeypatch.setenv("ESAJ_BRIDGE_MODE", "launch")
    assert bridge.bridge_status() == {"mode": "launch", "available": True, "endpoint": "", "reason": ""}
    assert fake_browser == []
    monkeypatch.setattr(bridge.shutil, "which", lambda name: None)
    assert bridge.bridge_status()["reason"] == "no_browser"
    monkeypatch.setenv("ESAJ_BRIDGE_MODE", "attach")
    assert bridge.bridge_status()["available"] is True
