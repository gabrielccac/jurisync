"""Tencent CAPTCHA solver pieces that run without a browser: gap finding, answer checks, model replies."""

import json
import unittest
from unittest import mock

from PIL import Image, ImageDraw

from jurisync.core import tencent_captcha as tc
from jurisync.core.session_pool import LoginFailed

SIZE = 60


def piece_mask():
    """A puzzle-like shape: a square with a round knob on top and on the right."""
    mask = Image.new("L", (SIZE, SIZE), 0)
    draw = ImageDraw.Draw(mask)
    draw.rectangle((8, 14, 46, 52), fill=255)
    draw.ellipse((20, 4, 34, 18), fill=255)
    draw.ellipse((40, 26, 54, 40), fill=255)
    return mask


def puzzle(gap_x, top=40, width=340, height=200):
    """A textured background with a darkened, light-outlined hole at ``(gap_x, top)`` and the matching piece."""
    background = Image.new("RGB", (width, height))
    draw = ImageDraw.Draw(background)
    for x in range(width):  # soft vertical stripes: edges everywhere, as in a photo
        shade = 120 + (x * 7) % 60
        draw.line((x, 0, x, height), fill=(shade // 2, shade, shade // 3))
    mask = piece_mask()
    piece = background.crop((gap_x, top, gap_x + SIZE, top + SIZE)).convert("RGBA")
    piece.putalpha(mask)
    dark = Image.new("RGB", (SIZE, SIZE), (30, 30, 30))
    region = background.crop((gap_x, top, gap_x + SIZE, top + SIZE))
    background.paste(Image.blend(region, dark, 0.6), (gap_x, top), mask)
    outline = mask.point(lambda value: 255 if value else 0)
    edges = Image.new("L", (SIZE, SIZE), 0)
    for x in range(SIZE):
        for y in range(SIZE):
            if outline.getpixel((x, y)) and any(
                not (0 <= x + dx < SIZE and 0 <= y + dy < SIZE) or not outline.getpixel((x + dx, y + dy))
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))
            ):
                edges.putpixel((x, y), 255)
    background.paste((235, 235, 235), (gap_x, top), edges)
    return background, piece


class GapTest(unittest.TestCase):
    def test_the_outline_match_finds_the_hole(self):
        for gap_x in (150, 213, 270):
            with self.subTest(gap_x=gap_x):
                background, piece = puzzle(gap_x)
                x, confident, peaks = tc.local_gap(background, piece, 40, start=70)
                self.assertLessEqual(abs(x - gap_x), 2)
                self.assertTrue(confident)
                self.assertEqual(peaks[0][1], x)

    def test_candidates_are_spread_local_maxima(self):
        scores = {0: 1, 1: 9, 2: 8, 3: 7, 30: 5, 31: 6, 60: 2}
        self.assertEqual(tc.candidates(scores, spread=12, count=3), [(9, 1), (6, 31), (2, 60)])

    def test_the_model_hint_picks_the_nearest_candidate_or_stands_alone(self):
        peaks = [(9, 100), (8, 220)]
        self.assertEqual(tc.choose_gap(peaks, None), 100)
        self.assertEqual(tc.choose_gap(peaks, 214), 220)
        self.assertEqual(tc.choose_gap(peaks, 300), 300)

    def test_model_gap_reads_one_number_inside_the_picture(self):
        background, _piece = puzzle(150)
        for reply, expected in (("215", 215), ("x = 12", 12), ("10 or 20", None), ("9999", None), ("none", None)):
            with self.subTest(reply=reply), mock.patch.object(tc, "_ask_model", return_value=reply):
                self.assertEqual(tc.model_gap(background, SIZE, 40, api_key="k", model="m"), expected)


class AnswerTest(unittest.TestCase):
    def test_only_a_real_accepted_ticket_is_returned(self):
        good = json.dumps({"ret": 0, "ticket": "tr03abc", "randstr": "@xyz", "appid": "1"})
        self.assertEqual(tc.accepted_answer(good), good)
        for bad in (
            json.dumps({"ret": 0, "ticket": "terror_1001_1_1700000000", "randstr": "@a", "errorCode": 1001}),
            json.dumps({"ret": 0, "ticket": "tr03abc", "randstr": "@a", "errorCode": 1001}),
            json.dumps({"ret": 2, "ticket": "", "randstr": ""}),
            json.dumps({"ret": 0, "ticket": "tr03abc", "randstr": ""}),
            json.dumps([1, 2]),
            "not json",
            None,
        ):
            with self.subTest(bad=bad), self.assertRaises(LoginFailed) as raised:
                tc.accepted_answer(bad)
            self.assertEqual(raised.exception.reason, "captcha")

    def test_points_must_be_pairs_inside_the_picture(self):
        self.assertEqual(tc.parse_points("Answer: [[10, 20], [30.5, 40]]", 100, 100), [(10, 20), (30.5, 40)])
        for reply in ("[]", "no points", "[[10, 200]]", '[["a", 1]]'):
            with self.subTest(reply=reply), self.assertRaises(LoginFailed):
                tc.parse_points(reply, 100, 100)


class LayoutTest(unittest.TestCase):
    def test_style_values(self):
        style = (
            "background-image: url(&quot;https://ca.turing.captcha.qcloud.com/cap?img_index=0&amp;sess=s1&quot;); "
            "background-position: -70.8333px -247.917px; background-size: 345.06px 313.69px; width: 60.7px;"
        )
        self.assertEqual(tc._style_url(style), "https://ca.turing.captcha.qcloud.com/cap?img_index=0&sess=s1")
        self.assertEqual(tc._style_pair(style, "background-position"), (-70.8333, -247.917))
        self.assertEqual(tc._style_pair(style, "background-size"), (345.06, 313.69))
        self.assertIsNone(tc._style_pair("width: 1px", "background-size"))

    def test_pictures_come_only_from_tencent_hosts(self):
        for url in ("http://ca.turing.captcha.qcloud.com/x", "https://example.com/x.png", "https://qcloud.com.evil/x"):
            with self.subTest(url=url), self.assertRaises(LoginFailed):
                tc._image(url)

    def test_a_page_without_the_widget_script_is_a_captcha_failure_and_nothing_is_shown(self):
        driver = mock.MagicMock()
        driver.execute_script.return_value = False
        with (
            mock.patch("seleniumbase.Driver", return_value=driver),
            mock.patch.object(tc, "_wait", return_value=False),
            self.assertRaises(LoginFailed) as raised,
        ):
            tc.solve("https://projudi.example.jus.br/projudi/PaginaPrincipal.jsp", api_key="k")
        self.assertEqual(raised.exception.reason, "captcha")
        shown = [call for call in driver.execute_script.call_args_list if call.args == ("captchaShow()",)]
        self.assertEqual(shown, [])
        driver.quit.assert_called_once()


if __name__ == "__main__":
    unittest.main()
