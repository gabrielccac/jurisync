"""AWS WAF image CAPTCHA ("Vamos confirmar que você é humano") in front of a portal.

Some portals (TJES PJe 1G) answer every request with HTTP 405 and an AWS WAF page that
asks the visitor to pick images in a 3x3 grid. A headless browser opens the page, a
vision model on OpenRouter picks the tiles that match the instruction, and once the
WAF accepts the answer it sets an ``aws-waf-token`` cookie. That cookie is copied into
the HTTP session, which then reaches the portal like any other.

The approach follows gabrielccac/captcha-solver (tiles + instruction -> JSON array of
tile numbers). The OpenRouter key comes from ``JURISYNC_CAPTCHA_API_KEY``, a machine setting shared by every
office on this computer, or else the office's own ``OPENROUTER_API_KEY``; ``JURISYNC_CAPTCHA_MODEL`` overrides the
default model.
"""

import base64
import io
import json
import logging
import re
import time

from jurisync.core import credentials

log = logging.getLogger("jurisync.awswaf")

API = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "meta/muse-spark-1.3"
TOKEN_COOKIE = "aws-waf-token"
MAX_PUZZLES = 6

PROMPT = (
    "You solve an AWS WAF image CAPTCHA. The instruction (Brazilian Portuguese) is: {instruction!r}. "
    "The images below are the 3x3 grid tiles in row-major order (1=top-left, 9=bottom-right). "
    "Decide which tiles match the instruction and reply with ONLY a JSON array of tile numbers "
    "(1-9), e.g. [1,5,6]. No other text."
)

_STATE_JS = """
const root = document.querySelector('#captcha-container');
if (!root) return {state: 'gone'};
const status = [...root.querySelectorAll('[role=status]')].map(e => e.innerText).join(' ');
const alert = [...root.querySelectorAll('[role=alert]')].filter(e => e.offsetParent).map(e => e.innerText).join(' ');
const canvas = root.querySelector('canvas');
const start = root.querySelector('#amzn-captcha-verify-button');
return {
  state: canvas && canvas.offsetParent ? 'grid' : (start && start.offsetParent ? 'start' : 'loading'),
  status: status, alert: alert,
  text: root.innerText.slice(0, 400),
};
"""


def captcha_api_key():
    """The OpenRouter key that pays for CAPTCHA answers: the shared machine key, else the office's own."""
    return credentials.machine("JURISYNC_CAPTCHA_API_KEY") or credentials.env("OPENROUTER_API_KEY")


def is_challenge(response):
    """True when a response is the AWS WAF CAPTCHA page instead of the portal."""
    if response.status_code not in (202, 405):
        return False
    text = response.text[:4000]
    return "awswaf.com" in text and "captcha" in text.lower()


def _instruction(text):
    match = re.search(r"(Escolha[^\n]+|Choose[^\n]+|Select[^\n]+)", text)
    return match.group(1).strip() if match else ""


def _tiles(png):
    from PIL import Image

    image = Image.open(io.BytesIO(png)).convert("RGB")
    width, height = image.size
    tiles = []
    for row in range(3):
        for col in range(3):
            box = (col * width // 3, row * height // 3, (col + 1) * width // 3, (row + 1) * height // 3)
            tiles.append(image.crop(box))
    return tiles


def _jpeg64(image, scale=2):
    from PIL import Image

    image = image.resize((image.width * scale, image.height * scale), Image.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=90)
    return base64.b64encode(buffer.getvalue()).decode()


def pick_tiles(instruction, tiles, *, api_key, model, timeout=90):
    """Ask the vision model which of the 9 tiles match; return 0-based indices."""
    from curl_cffi import requests

    content = [{"type": "text", "text": PROMPT.format(instruction=instruction)}]
    for index, tile in enumerate(tiles, 1):
        content.append({"type": "text", "text": f"Tile {index}:"})
        content.append({"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + _jpeg64(tile)}})
    body = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "max_tokens": 4096,
        "reasoning": {"effort": "minimal"},
    }
    response = requests.post(API, headers={"Authorization": f"Bearer {api_key}"}, json=body, timeout=timeout)
    response.raise_for_status()
    reply = response.json()["choices"][0]["message"]["content"] or ""
    match = re.search(r"\[[\d,\s]*\]", reply)
    if not match:
        raise RuntimeError("CAPTCHA model reply has no tile list")
    return sorted({int(value) - 1 for value in json.loads(match.group(0)) if 1 <= int(value) <= 9})


def _wait_state(driver, wanted, timeout=20):
    deadline = time.monotonic() + timeout
    state = {}
    while time.monotonic() < deadline:
        try:
            state = driver.execute_script(_STATE_JS)
        except Exception:
            state = {"state": "loading"}  # the page is reloading after the voucher was accepted
        if state.get("state") in wanted:
            return state
        time.sleep(0.5)
    return state


def solve(url, *, api_key=None, model=None, max_puzzles=MAX_PUZZLES, debug_dir=None):
    """Pass the WAF CAPTCHA at ``url`` in a headless browser; return its cookies."""
    from seleniumbase import Driver

    api_key = api_key or captcha_api_key()
    if not api_key:
        raise RuntimeError(
            "The portal shows an AWS WAF CAPTCHA: set JURISYNC_CAPTCHA_API_KEY (or OPENROUTER_API_KEY) to solve it"
        )
    model = model or credentials.machine("JURISYNC_CAPTCHA_MODEL") or DEFAULT_MODEL
    driver = Driver(uc=True, headless=True, locale="pt-BR")
    try:
        driver.set_window_size(1280, 900)
        driver.get(url)
        state = _wait_state(driver, {"start", "grid", "gone"})
        if state.get("state") == "start":
            driver.execute_script("document.querySelector('#amzn-captcha-verify-button').click()")
            state = _wait_state(driver, {"grid", "gone"})
        for puzzle in range(1, max_puzzles + 1):
            if state.get("state") == "gone":
                break
            if state.get("state") != "grid":
                raise RuntimeError("AWS WAF CAPTCHA grid did not load")
            time.sleep(1)  # the canvas paints after the element appears
            instruction = _instruction(driver.execute_script(_STATE_JS).get("text", ""))
            canvas = driver.find_element("css selector", "#captcha-container canvas")
            picks = pick_tiles(instruction, _tiles(canvas.screenshot_as_png), api_key=api_key, model=model)
            log.info("AWS WAF puzzle %d: %r -> tiles %s", puzzle, instruction, [index + 1 for index in picks])
            buttons = driver.find_elements("css selector", "#captcha-container button[role=checkbox]")
            for index in picks:
                buttons[index].click()
                time.sleep(0.3)
            confirm = next(
                button
                for button in driver.find_elements("css selector", "#captcha-container button")
                if button.text.strip() in ("Confirmar", "Confirm")
            )
            if debug_dir:
                driver.save_screenshot(f"{debug_dir}/waf-{puzzle}-picked.png")
            confirm.click()
            time.sleep(3)
            state = _wait_state(driver, {"grid", "gone"}, timeout=20)
            if debug_dir:
                driver.save_screenshot(f"{debug_dir}/waf-{puzzle}-after.png")
                log.info("AWS WAF after puzzle %d: %s", puzzle, {k: state.get(k) for k in ("state", "status", "alert")})
        # The accepted voucher reloads the page into the portal; the challenge's own token alone is not enough.
        final = _wait_state(driver, {"gone"}, timeout=15)
        if debug_dir:
            driver.save_screenshot(f"{debug_dir}/waf-final.png")
            log.info("AWS WAF final: %s at %s; token=%s", final.get("state"), driver.current_url, _token(driver))
        if final.get("state") != "gone" or not _token(driver):
            raise RuntimeError("AWS WAF CAPTCHA was not accepted")
        return _all_cookies(driver)
    finally:
        driver.quit()


def _all_cookies(driver):
    # get_cookies() only covers the current page's domain, and the solved page has moved on to the SSO.
    return driver.execute_cdp_cmd("Network.getAllCookies", {})["cookies"]


def _token(driver):
    return any(cookie.get("name") == TOKEN_COOKIE for cookie in _all_cookies(driver))


def apply(session, cookies):
    """Copy the WAF token cookie(s) from the browser into a curl_cffi session."""
    for cookie in cookies:
        if cookie.get("name") != TOKEN_COOKIE:
            continue
        session.cookies.set(
            cookie["name"], cookie["value"], domain=cookie.get("domain") or "", path=cookie.get("path") or "/"
        )
    return session
