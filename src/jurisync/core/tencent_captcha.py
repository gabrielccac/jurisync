"""Tencent Cloud CAPTCHA (TCaptcha) in front of a login form, answered in a headless browser.

Some portals (Projudi TJBA) never post a login without a TCaptcha answer: the page builds
``new TencentCaptcha(appId, callback)``, its submit calls ``captcha.show()``, and the widget's callback hands
``{"ret": 0, "ticket", "randstr", "appid"}`` to a page function (``onSubmit`` in Projudi) that puts the JSON in a
hidden field and posts the form. The portal checks the ticket with Tencent on its side.

``solve`` opens the page in headless Chrome, replaces that page function so the browser posts nothing, shows the
widget and answers it like a person:

- slider ("Deslize para completar o quebra-cabeça"): the background (with a darker puzzle-shaped gap) and the sprite
  holding the piece are downloaded; the gap is found locally by matching the piece's outline against the
  background's edges along the piece's row (PIL only, ~0.2 s). When the local match is weak, or a previous answer of
  this login was refused, a vision model on OpenRouter (``JURISYNC_CAPTCHA_API_KEY``, as ``core.awswaf``) points at
  the gap and the closest local candidate is used. The slider is then dragged with an eased, slightly uneven motion;
  the piece follows it one to one;
- click/select puzzles (an instruction and a picture to click in): the picture and the instruction go to the vision
  model, which answers the points to click in order; then "OK"/"Confirmar". NOT seen live yet (TJBA served only
  sliders in November 2026): kept as a best effort.

A refused answer makes the widget draw a new puzzle; at most ``max_puzzles`` are answered. The widget's emergency
path (``terror_1001_...`` tickets the page script fakes when Tencent's script does not load) is never used: a missing
widget or such a ticket is a ``LoginFailed(reason="captcha")``, never sent.

The result is the answer JSON exactly as the page would put in its field, plus the browser's cookies for the page
host (the portal session the answer was given in), for the caller to post its login with its own HTTP session.
"""

import base64
import io
import json
import logging
import random
import re
import time
import urllib.parse

from jurisync.core import credentials
from jurisync.core.awswaf import API, DEFAULT_MODEL, captcha_api_key
from jurisync.core.session_pool import LoginFailed

log = logging.getLogger("jurisync.tencent_captcha")

MAX_PUZZLES = 5
IFRAME = "iframe[id^=tcaptcha_iframe], iframe[src*='turing.captcha']"
IMAGE_HOSTS = (".captcha.qcloud.com", ".captcha.gtimg.com")
# The local match is trusted when its best score beats the best score elsewhere (outside the peak) by this much.
CONFIDENT = 1.25

_LAYOUT_JS = """
const text = s => ((document.querySelector(s) || {}).innerText || '').trim();
const bg = document.querySelector('#slideBg');
const shown = e => { const s = getComputedStyle(e); return s.display !== 'none' && s.visibility !== 'hidden'; };
const cover = [...document.querySelectorAll('.tc-cover.tc-loading, .tc-cover.tc-success')].some(shown);
const items = [...document.querySelectorAll('.tc-fg-item')].map(e => ({
  slider: e.classList.contains('tc-slider-normal'), style: e.getAttribute('style') || '',
  left: parseFloat(e.style.left) || 0, top: parseFloat(e.style.top) || 0,
  width: e.getBoundingClientRect().width, height: e.getBoundingClientRect().height,
}));
return {
  instruction: text('#instructionText') || text('#instruction'),
  note: text('#tcaptcha_note'), error: text('#statusError'),
  background: bg ? bg.getAttribute('style') || '' : '',
  backgroundWidth: bg ? bg.getBoundingClientRect().width : 0,
  busy: cover, items: items,
};
"""
_CAPTURE_JS = """
window.__jurisyncCaptcha = null;
window[arguments[0]] = function (answer) { window.__jurisyncCaptcha = String(answer); };
return typeof TencentCaptcha === 'function';
"""

SLIDER_PROMPT = (
    "This {width}x{height} picture is a slider CAPTCHA background. A puzzle-piece-shaped hole (a darker area with a "
    "light outline, about {size} px wide) is somewhere in the horizontal band from y={top} to y={bottom}. Reply with "
    "ONLY the x pixel coordinate of the LEFT edge of that hole, as an integer. No other text."
)
CLICK_PROMPT = (
    "You solve a CAPTCHA. The instruction (Portuguese) is: {instruction!r}. The picture is {width}x{height} pixels. "
    "Reply with ONLY a JSON array of the points to click, in the required order, as [x, y] pixel pairs, e.g. "
    "[[120, 45], [30, 80]]. No other text."
)


def _style_url(style):
    match = re.search(r'url\(\s*["\']?([^"\')]+)', (style or "").replace("&quot;", '"').replace("&amp;", "&"))
    return match.group(1) if match else ""


def _style_pair(style, prop):
    match = re.search(rf"{prop}:\s*(-?[\d.]+)px\s+(-?[\d.]+)px", style or "")
    return (float(match.group(1)), float(match.group(2))) if match else None


def _image(url, *, timeout=30):
    """A puzzle picture from Tencent's CAPTCHA hosts (https only, no other host)."""
    from curl_cffi import requests
    from PIL import Image

    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not host.endswith(IMAGE_HOSTS):
        raise LoginFailed("TCaptcha picture is not on a Tencent CAPTCHA host", reason="captcha")
    response = requests.get(
        url, impersonate="chrome", timeout=timeout, headers={"Referer": "https://global.turing.captcha.gtimg.com/"}
    )
    if response.status_code != 200:
        raise LoginFailed(f"TCaptcha picture failed (HTTP {response.status_code})", reason="captcha")
    return Image.open(io.BytesIO(response.content))


# --- Slider: where the gap is ----------------------------------------------------------------------------------------


def piece_outline(piece):
    """``(points, width, height)``: the border pixels of the piece's opaque area (alpha > 128)."""
    mask = piece.convert("RGBA").split()[3].point(lambda alpha: 255 if alpha > 128 else 0)
    width, height = mask.size
    opaque = mask.load()

    def inside(x, y):
        return 0 <= x < width and 0 <= y < height and opaque[x, y]

    points = [
        (x, y)
        for y in range(height)
        for x in range(width)
        if opaque[x, y] and not all(inside(x + dx, y + dy) for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)))
    ]
    return points, width, height


def gap_scores(background, piece, top, *, start=0):
    """``{x: score}``: how strongly the background has edges along the piece outline placed at ``(x, top)``."""
    from PIL import ImageFilter

    points, width, _height = piece_outline(piece)
    if not points:
        raise LoginFailed("TCaptcha piece has no opaque area", reason="captcha")
    gray = background.convert("L")
    edges = gray.filter(ImageFilter.FIND_EDGES).load()
    bg_width, bg_height = gray.size
    rows = [(x, top + y) for x, y in points if 0 <= top + y < bg_height]
    return {
        left: sum(edges[left + x, y] for x, y in rows) / len(points)
        for left in range(max(0, start), bg_width - width + 1)
    }


def candidates(scores, *, spread=12, count=4):
    """The best ``count`` local maxima of ``scores``, ``[(score, x)]`` best first, at least ``spread`` px apart."""
    found = []
    for score, x in sorted(((score, x) for x, score in scores.items()), reverse=True):
        if all(abs(x - other) >= spread for _score, other in found):
            found.append((score, x))
            if len(found) == count:
                break
    return found


def local_gap(background, piece, top, *, start=0):
    """``(x, confident, candidates)``: the gap's left edge in background pixels by outline matching."""
    peaks = candidates(gap_scores(background, piece, top, start=start))
    if not peaks:
        raise LoginFailed("TCaptcha slider has no room for the piece", reason="captcha")
    confident = len(peaks) == 1 or peaks[0][0] >= CONFIDENT * max(peaks[1][0], 1e-6)
    return peaks[0][1], confident, peaks


def _png64(image, scale=1):
    from PIL import Image

    image = image.convert("RGB")
    if scale != 1:
        image = image.resize((int(image.width * scale), int(image.height * scale)), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode()


def _ask_model(prompt, image, *, api_key, model, timeout=90):
    from curl_cffi import requests

    content = [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + _png64(image)}},
    ]
    body = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "max_tokens": 4096,
        "reasoning": {"effort": "minimal"},
    }
    response = requests.post(API, headers={"Authorization": f"Bearer {api_key}"}, json=body, timeout=timeout)
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"] or ""


def model_gap(background, size, top, *, api_key, model):
    """The gap's left edge as the vision model sees it, or None when the reply has no usable number."""
    prompt = SLIDER_PROMPT.format(
        width=background.width, height=background.height, size=size, top=top, bottom=top + size
    )
    reply = _ask_model(prompt, background, api_key=api_key, model=model)
    numbers = [int(value) for value in re.findall(r"-?\d+", reply)]
    return numbers[0] if len(numbers) == 1 and 0 <= numbers[0] < background.width else None


def choose_gap(peaks, hint, *, near=30):
    """The local candidate closest to the model's ``hint``; the hint itself when no candidate is near it."""
    if hint is None:
        return peaks[0][1]
    closest = min(peaks, key=lambda peak: abs(peak[1] - hint))
    return closest[1] if abs(closest[1] - hint) <= near else hint


def parse_points(reply, width, height):
    """``[(x, y)]`` from a model reply like ``[[10, 20], [30, 40]]``; points outside the picture are refused."""
    match = re.search(r"\[\s*\[.*\]\s*\]", reply or "", re.S)
    try:
        pairs = json.loads(match.group(0)) if match else []
    except ValueError:
        pairs = []
    points = []
    for pair in pairs:
        if not (isinstance(pair, list) and len(pair) == 2 and all(isinstance(v, (int, float)) for v in pair)):
            raise LoginFailed("CAPTCHA model reply is not a list of points", reason="captcha")
        x, y = pair
        if not (0 <= x < width and 0 <= y < height):
            raise LoginFailed("CAPTCHA model pointed outside the picture", reason="captcha")
        points.append((x, y))
    if not points:
        raise LoginFailed("CAPTCHA model reply has no points", reason="captcha")
    return points


# --- Answer checks ---------------------------------------------------------------------------------------------------


def accepted_answer(raw):
    """The widget's answer when Tencent accepted it (``ret`` 0 with a real ticket), else ``LoginFailed``.

    A ``terror_`` ticket or an ``errorCode`` is the page script's emergency path (Tencent's script did not load):
    never sent, since it would skip the CAPTCHA instead of solving it.
    """
    try:
        answer = json.loads(raw)
    except (TypeError, ValueError):
        raise LoginFailed("TCaptcha answer is not JSON", reason="captcha") from None
    ticket = str(answer.get("ticket") or "") if isinstance(answer, dict) else ""
    if (
        not isinstance(answer, dict)
        or str(answer.get("ret")) != "0"
        or not ticket
        or ticket.startswith("terror_")
        or answer.get("errorCode")
        or not answer.get("randstr")
    ):
        raise LoginFailed("TCaptcha did not accept an answer (no valid ticket)", reason="captcha")
    return raw


# --- Browser ---------------------------------------------------------------------------------------------------------


def _wait(check, timeout, step=0.3):
    deadline = time.monotonic() + timeout
    while True:
        value = check()
        if value or time.monotonic() >= deadline:
            return value
        time.sleep(step)


class _Widget:
    """The TCaptcha iframe of one page, driven through Selenium."""

    def __init__(self, driver):
        self.driver = driver

    def answer(self):
        self.driver.switch_to.default_content()
        return self.driver.execute_script("return window.__jurisyncCaptcha")

    def frame(self):
        """Switch into the visible widget iframe; False when there is none (closed or not shown)."""
        self.driver.switch_to.default_content()
        for frame in self.driver.find_elements("css selector", IFRAME):
            if frame.is_displayed():
                self.driver.switch_to.frame(frame)
                return True
        return False

    def layout(self):
        """The puzzle as drawn, once its pictures are in place; ``None`` while it loads or after it closed."""
        if not self.frame():
            return None
        state = self.driver.execute_script(_LAYOUT_JS)
        if state["busy"] or not state["background"] or not _style_url(state["background"]):
            return None
        return state

    def wait_puzzle(self, timeout=20):
        return _wait(lambda: self.answer() or self.layout(), timeout)


def _drag(driver, handle, distance):
    from selenium.webdriver.common.action_chains import ActionChains

    actions = ActionChains(driver, duration=random.randint(8, 16))
    actions.click_and_hold(handle).pause(random.uniform(0.15, 0.35))
    steps, done = random.randint(18, 28), 0
    for step in range(1, steps + 1):
        progress = step / steps
        target = distance * (1 - (1 - progress) ** 3)  # fast start, slow arrival
        delta = round(target - done)
        if delta:
            actions.move_by_offset(delta, random.choice((0, 0, 0, 1, -1)))
            done += delta
    if round(distance - done):
        actions.move_by_offset(round(distance - done), 0)
    actions.pause(random.uniform(0.25, 0.5)).release().perform()


def _solve_slider(driver, state, *, attempt, api_key, model, record):
    piece = next(
        (item for item in state["items"] if not item["slider"] and _style_url(item["style"]) and item["width"] < 150),
        None,
    )
    if piece is None or not any(item["slider"] for item in state["items"]):
        raise LoginFailed("TCaptcha slider has no piece", reason="captcha")
    background = _image(_style_url(state["background"])).convert("RGB")
    sprite = _image(_style_url(piece["style"]))
    scale = background.width / state["backgroundWidth"]
    position = _style_pair(piece["style"], "background-position")
    sprite_size = _style_pair(piece["style"], "background-size")
    if position is None or sprite_size is None:
        raise LoginFailed("TCaptcha piece has an unexpected layout", reason="captcha")
    sprite_scale = sprite.width / sprite_size[0]
    left, top = round(-position[0] * sprite_scale), round(-position[1] * sprite_scale)
    size = round(piece["width"] * sprite_scale)
    cut = sprite.crop((left, top, left + size, top + size))
    piece_top = round(piece["top"] * scale)
    start = round((piece["left"] + piece["width"] * 0.6) * scale)  # the gap is never under the piece's start
    x, confident, peaks = local_gap(background, cut, piece_top, start=start)
    record["source"] = "local"
    if (attempt or not confident) and api_key:
        try:
            hint = model_gap(background, size, piece_top, api_key=api_key, model=model)
        except Exception as error:  # the local answer still goes; the model is only a tie-breaker
            log.info("TCaptcha model hint failed: %s", type(error).__name__)
            hint = None
        if hint is not None:
            x = choose_gap(peaks, hint)
            record["source"] = "local+model"
    distance = x / scale - piece["left"]
    record.update(confident=confident, distance=round(distance, 1))
    handle = driver.find_element("css selector", ".tc-slider-normal")
    _drag(driver, handle, distance)


def _solve_click(driver, state, *, api_key, model, record):
    from PIL import Image
    from selenium.webdriver.common.action_chains import ActionChains

    if not api_key:
        raise LoginFailed(
            "The portal shows a TCaptcha picture puzzle: set JURISYNC_CAPTCHA_API_KEY to solve it", reason="captcha"
        )
    area = next(
        (
            element
            for selector in ("#slideBg", "#tcImgArea", ".tc-bg-img")
            for element in driver.find_elements("css selector", selector)
            if element.is_displayed()
        ),
        None,
    )
    if area is None:
        raise LoginFailed("TCaptcha picture puzzle has no picture", reason="captcha")
    picture = Image.open(io.BytesIO(area.screenshot_as_png))
    css_width, css_height = area.size["width"], area.size["height"]
    reply = _ask_model(
        CLICK_PROMPT.format(instruction=state["instruction"], width=picture.width, height=picture.height),
        picture,
        api_key=api_key,
        model=model,
    )
    points = parse_points(reply, picture.width, picture.height)
    record.update(source="model", points=len(points))
    ratio_x, ratio_y = css_width / picture.width, css_height / picture.height
    for x, y in points:
        # move_to_element_with_offset counts from the element's center.
        ActionChains(driver).move_to_element_with_offset(
            area, round(x * ratio_x - css_width / 2), round(y * ratio_y - css_height / 2)
        ).pause(random.uniform(0.3, 0.7)).click().perform()
    confirm = next(
        (
            element
            for element in driver.find_elements("css selector", ".verify-btn, .tc-action.verify-btn, button")
            if element.is_displayed() and element.text.strip() in ("OK", "Confirmar", "Confirm", "Verificar")
        ),
        None,
    )
    if confirm is not None:
        confirm.click()


def solve(
    page_url,
    *,
    capture="onSubmit",
    show="captchaShow()",
    max_puzzles=MAX_PUZZLES,
    api_key=None,
    model=None,
    debug_dir=None,
    attempts=None,
):
    """Answer the TCaptcha that ``show`` opens on ``page_url``; return ``(answer_json, cookies)``.

    ``capture`` is the page function the widget's callback hands the answer to; it is replaced, so the browser
    posts nothing. ``attempts`` (a list) receives one record per puzzle answered: kind, source, accepted.
    Raises ``LoginFailed(reason="captcha")`` when no answer was accepted.
    """
    from seleniumbase import Driver

    api_key = api_key or captcha_api_key()
    model = model or credentials.machine("JURISYNC_CAPTCHA_MODEL") or DEFAULT_MODEL
    attempts = attempts if attempts is not None else []
    driver = Driver(uc=True, headless=True, locale="pt-BR")
    try:
        driver.set_window_size(1280, 900)
        driver.get(page_url)
        if not _wait(lambda: driver.execute_script(_CAPTURE_JS, capture), 20, step=0.5):
            raise LoginFailed("TCaptcha script did not load on the login page", reason="captcha")
        driver.execute_script(show)
        widget = _Widget(driver)
        for attempt in range(max_puzzles):
            state = widget.wait_puzzle()
            if isinstance(state, str):
                break  # answered (the widget can pass a returning visitor without a puzzle)
            if not state:
                raise LoginFailed("TCaptcha puzzle did not load", reason="captcha")
            slider = any(item["slider"] for item in state["items"])
            record = {"kind": "slider" if slider else "click", "instruction": state["instruction"][:80]}
            attempts.append(record)
            if slider:
                _solve_slider(driver, state, attempt=attempt, api_key=api_key, model=model, record=record)
            else:
                _solve_click(driver, state, api_key=api_key, model=model, record=record)
            answered = _wait(widget.answer, 4)
            record["accepted"] = bool(answered)
            if debug_dir:
                driver.save_screenshot(f"{debug_dir}/tcaptcha-{attempt + 1}.png")
            log.info("TCaptcha puzzle %d: %s", attempt + 1, record)
            if answered:
                break
            time.sleep(1.5)  # the widget shows its error, then draws a new puzzle
        raw = widget.answer()
        if not raw:
            raise LoginFailed(f"TCaptcha refused {len(attempts)} answer(s)", reason="captcha")
        answer = accepted_answer(raw)
        host = urllib.parse.urlsplit(page_url).hostname
        cookies = [cookie for cookie in driver.get_cookies() if (cookie.get("domain") or "").lstrip(".") == host]
        return answer, cookies
    finally:
        driver.quit()


def apply(session, cookies):
    """Copy the page's cookies (its session) from the browser into a curl_cffi session."""
    for cookie in cookies:
        session.cookies.set(
            cookie["name"], cookie["value"], domain=cookie.get("domain") or "", path=cookie.get("path") or "/"
        )
    return session
