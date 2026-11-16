"""Offline checks for the AWS WAF CAPTCHA helper."""

import io
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from PIL import Image

from jurisync.core import awswaf

WAF_PAGE = (
    '<title>Human Verification</title><script src="https://abc.sa-east-1.captcha.awswaf.com/abc/captcha.js"></script>'
)


@pytest.mark.parametrize(
    ("status", "text", "expected"),
    [
        (405, WAF_PAGE, True),
        (202, WAF_PAGE, True),
        (200, WAF_PAGE, False),
        (405, "<html>Method Not Allowed</html>", False),
    ],
)
def test_is_challenge(status, text, expected):
    assert awswaf.is_challenge(SimpleNamespace(status_code=status, text=text)) is expected


def test_instruction_is_read_from_the_widget_text():
    text = "Vamos confirmar que você é humano\nEscolha todos(as) as cadeiras\nSelecionar idioma"
    assert awswaf._instruction(text) == "Escolha todos(as) as cadeiras"


def test_canvas_is_split_into_nine_row_major_tiles():
    image = Image.new("RGB", (300, 300))
    image.paste((255, 0, 0), (200, 0, 300, 100))  # tile 3: top right
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    tiles = awswaf._tiles(buffer.getvalue())
    assert len(tiles) == 9
    assert tiles[2].getpixel((50, 50)) == (255, 0, 0)
    assert tiles[0].getpixel((50, 50)) == (0, 0, 0)


def test_pick_tiles_returns_zero_based_indices_in_range():
    reply = Mock()
    reply.json.return_value = {"choices": [{"message": {"content": "Tiles: [3, 1, 12, 3]"}}]}
    with patch("curl_cffi.requests.post", return_value=reply) as post:
        picks = awswaf.pick_tiles("Escolha os baldes", [Image.new("RGB", (10, 10))] * 9, api_key="k", model="m")
    assert picks == [0, 2]
    assert post.call_args.kwargs["json"]["model"] == "m"


def test_pick_tiles_rejects_a_reply_without_a_list():
    reply = Mock()
    reply.json.return_value = {"choices": [{"message": {"content": "I cannot help"}}]}
    with patch("curl_cffi.requests.post", return_value=reply), pytest.raises(RuntimeError):
        awswaf.pick_tiles("Escolha", [Image.new("RGB", (10, 10))] * 9, api_key="k", model="m")


def test_solve_needs_an_openrouter_key(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("JURISYNC_CAPTCHA_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="JURISYNC_CAPTCHA_API_KEY"):
        awswaf.solve("https://pje.tjes.jus.br/pje/login.seam")


def test_the_shared_captcha_key_comes_before_the_office_key(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "office")
    monkeypatch.setenv("JURISYNC_CAPTCHA_API_KEY", "shared")
    assert awswaf.captcha_api_key() == "shared"
    monkeypatch.delenv("JURISYNC_CAPTCHA_API_KEY")
    assert awswaf.captcha_api_key() == "office"


def test_apply_copies_only_the_waf_token():
    session = Mock()
    awswaf.apply(
        session,
        [
            {"name": "aws-waf-token", "value": "t", "domain": ".pje.tjes.jus.br", "path": "/"},
            {"name": "JSESSIONID", "value": "s", "domain": "pje.tjes.jus.br"},
        ],
    )
    session.cookies.set.assert_called_once_with("aws-waf-token", "t", domain=".pje.tjes.jus.br", path="/")
