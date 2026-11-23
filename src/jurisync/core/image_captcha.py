"""Short text CAPTCHA (a code typed from a picture): local OCR first, then a vision model on OpenRouter.

The Infra framework behind classic Eproc (TJTO) asks, after user and password, for a 4-character code (letters and
digits, compared without case) drawn in one dark color over colored lines and a varying background. A wrong answer
only draws a new picture, so a cheap first guess is worth it.

1. Local: when the optional ``ddddocr`` package is installed (``captcha-ocr`` extra), the picture is reduced to its
   dark, unsaturated pixels and read offline (~10 ms). Only a reply of exactly ``length`` letters/digits is used.
2. Vision model: otherwise, and from the second picture of a login on, the picture goes to a vision model, as
   ``core.awswaf`` does for the AWS WAF grid. ``JURISYNC_CAPTCHA_API_KEY`` (machine-wide, falling back to
   ``OPENROUTER_API_KEY``) pays for it; ``JURISYNC_CAPTCHA_MODEL`` overrides the default model.

Benchmark on 60 TJTO pictures (16/11/2026; the second 30 not used to tune the filter): vision model 56/60 exact,
59/60 counting l/I/1 alike, ~2.3 s and ~US$0.0006 each; local OCR 45/60 without case (18/30 on the untuned half),
and when it does give 4 characters they are right 45 times in 50.

A reply that is not a code of the expected shape raises ``UnreadableCaptcha``; the caller asks for a new picture
rather than sending a guess.
"""

import base64
import io
import logging
import re

from jurisync.core import credentials
from jurisync.core.awswaf import API, DEFAULT_MODEL, captcha_api_key
from jurisync.core.session_pool import LoginFailed

log = logging.getLogger("jurisync.image_captcha")

PROMPT = (
    "This image is a CAPTCHA with exactly {length} characters (letters A-Z or a-z and digits 0-9), drawn over "
    "colored lines and a noisy background. Read the characters from left to right and reply with ONLY those "
    "{length} characters, no spaces, quotes or other text."
)
# The text is one dark color; the noise lines and backgrounds are lighter or saturated.
DARK, GRAY = 150, 130

_local_reader = None


class UnreadableCaptcha(LoginFailed):
    """No code of the expected shape could be read from the picture."""

    reason = "captcha"


def data_uri_bytes(src):
    """The image bytes of a ``data:image/...;base64,`` URI (how Infra embeds the CAPTCHA)."""
    head, _, payload = (src or "").partition(",")
    if not head.startswith("data:image/") or ";base64" not in head or not payload:
        raise UnreadableCaptcha("CAPTCHA image is not an embedded base64 picture")
    return base64.b64decode(payload)


def code_of(text, length=4):
    """``text`` when it is exactly ``length`` letters/digits, else None."""
    return text if re.fullmatch(rf"[A-Za-z0-9]{{{length}}}", text or "") else None


def dark_text(image_bytes, scale=2):
    """Black where a pixel is dark and nearly gray (the code), white elsewhere; PNG bytes."""
    from PIL import Image

    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    mask = Image.new("L", image.size, 255)
    pixels, marks = image.load(), mask.load()
    for y in range(image.height):
        for x in range(image.width):
            if max(pixels[x, y]) < DARK and max(pixels[x, y]) - min(pixels[x, y]) < GRAY:
                marks[x, y] = 0
    mask = mask.resize((image.width * scale, image.height * scale))
    buffer = io.BytesIO()
    mask.save(buffer, format="PNG")
    return buffer.getvalue()


def read_locally(image_bytes, length=4):
    """The code read by the local OCR, or None (not installed, or not a code of the expected shape)."""
    global _local_reader
    if _local_reader is None:
        try:
            import ddddocr
        except ImportError:
            _local_reader = False
        else:
            _local_reader = ddddocr.DdddOcr(show_ad=False, beta=True)
    if not _local_reader:
        return None
    text = re.sub(r"[^A-Za-z0-9]", "", _local_reader.classification(dark_text(image_bytes)))
    return code_of(text, length)


def _png64(image_bytes, scale=3):
    from PIL import Image

    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    image = image.resize((image.width * scale, image.height * scale), Image.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode()


def parse_reply(reply, length=4):
    """The code in a model reply (``"Answer: aB3x"`` -> ``"aB3x"``), or ``UnreadableCaptcha``."""
    text = (reply or "").strip().strip("`'\"").strip()
    if code_of(text, length):
        return text
    found = re.findall(rf"(?<![A-Za-z0-9])[A-Za-z0-9]{{{length}}}(?![A-Za-z0-9])", text)
    if len(found) == 1:
        return found[0]
    raise UnreadableCaptcha(f"CAPTCHA model reply is not a {length}-character code")


def read_with_model(image_bytes, *, length=4, api_key=None, model=None, timeout=60):
    """The code as the vision model reads it."""
    from curl_cffi import requests

    api_key = api_key or captcha_api_key()
    if not api_key:
        raise LoginFailed(
            "The portal asks for an image CAPTCHA: set JURISYNC_CAPTCHA_API_KEY to solve it", reason="captcha"
        )
    model = model or credentials.machine("JURISYNC_CAPTCHA_MODEL") or DEFAULT_MODEL
    content = [
        {"type": "text", "text": PROMPT.format(length=length)},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + _png64(image_bytes)}},
    ]
    body = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "max_tokens": 4096,
        "reasoning": {"effort": "minimal"},
    }
    response = requests.post(API, headers={"Authorization": f"Bearer {api_key}"}, json=body, timeout=timeout)
    response.raise_for_status()
    return parse_reply(response.json()["choices"][0]["message"]["content"], length)


def solve(image_bytes, *, length=4, attempt=0, api_key=None, model=None):
    """``(code, source)`` for the picture: the local OCR on a login's first picture, the vision model otherwise."""
    if attempt == 0:
        code = read_locally(image_bytes, length)
        if code:
            log.info("image CAPTCHA read locally")
            return code, "local"
    code = read_with_model(image_bytes, length=length, api_key=api_key, model=model)
    log.info("image CAPTCHA read by the vision model")
    return code, "model"
