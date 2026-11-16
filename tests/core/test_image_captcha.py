import io
import os
import unittest
from unittest import mock

from PIL import Image

from jurisync.core import image_captcha


def png(colors):
    image = Image.new("RGB", (len(colors), 1))
    image.putdata(colors)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


class ReplyParsing(unittest.TestCase):
    def test_codes_are_four_letters_or_digits(self):
        self.assertEqual(image_captcha.parse_reply(" aB3x\n"), "aB3x")
        self.assertEqual(image_captcha.parse_reply("`Zz99`"), "Zz99")
        self.assertEqual(image_captcha.parse_reply("Answer: q3RR"), "q3RR")
        for reply in ("", "abc", "abcde", "ab cd", "a-b3", "one ABCD two EFGH", None):
            with self.subTest(reply=reply), self.assertRaises(image_captcha.UnreadableCaptcha):
                image_captcha.parse_reply(reply)

    def test_embedded_picture_is_decoded(self):
        self.assertEqual(image_captcha.data_uri_bytes("data:image/png;base64,UElD"), b"PIC")
        for src in ("", "https://x/captcha.png", "data:text/html;base64,UElD", "data:image/png,PIC"):
            with self.subTest(src=src), self.assertRaises(image_captcha.UnreadableCaptcha):
                image_captcha.data_uri_bytes(src)


class DarkText(unittest.TestCase):
    def test_keeps_only_dark_gray_pixels(self):
        # dark green text, magenta line, light background, dark saturated blue line
        mask = Image.open(
            io.BytesIO(
                image_captcha.dark_text(png([(20, 60, 40), (255, 0, 255), (220, 220, 230), (0, 0, 255)]), scale=1)
            )
        )
        self.assertEqual([mask.getpixel((x, 0)) for x in range(4)], [0, 255, 255, 255])


class Solving(unittest.TestCase):
    def test_local_reading_first_then_the_model(self):
        with (
            mock.patch.object(image_captcha, "read_locally", return_value="aB3x") as local,
            mock.patch.object(image_captcha, "read_with_model", return_value="Zz99") as model,
        ):
            self.assertEqual(image_captcha.solve(b"img"), ("aB3x", "local"))
            self.assertEqual(image_captcha.solve(b"img", attempt=1), ("Zz99", "model"))
        local.assert_called_once()
        model.assert_called_once()

    def test_model_when_the_local_reading_has_no_code(self):
        with (
            mock.patch.object(image_captcha, "read_locally", return_value=None),
            mock.patch.object(image_captcha, "read_with_model", return_value="Zz99"),
        ):
            self.assertEqual(image_captcha.solve(b"img"), ("Zz99", "model"))

    def test_without_ocr_package_local_reading_is_skipped(self):
        with mock.patch.object(image_captcha, "_local_reader", False):
            self.assertIsNone(image_captcha.read_locally(b"img"))

    def test_local_reading_must_have_the_code_shape(self):
        reader = mock.Mock()
        with mock.patch.object(image_captcha, "_local_reader", reader):
            reader.classification.return_value = "日b33b"
            self.assertEqual(image_captcha.read_locally(png([(0, 0, 0)])), "b33b")
            reader.classification.return_value = "pyx"
            self.assertIsNone(image_captcha.read_locally(png([(0, 0, 0)])))

    def test_machine_wide_key_comes_first_and_one_is_required(self):
        from jurisync.core import credentials

        with mock.patch.dict(os.environ, {"JURISYNC_CAPTCHA_API_KEY": "shared"}):
            self.assertEqual(image_captcha.captcha_api_key(), "shared")
            # Hosted: the server's key serves every office; an office's vault entry cannot replace it.
            with credentials.scope("office-a", {"OPENROUTER_API_KEY": "customer", "JURISYNC_CAPTCHA_API_KEY": "x"}):
                self.assertEqual(image_captcha.captcha_api_key(), "shared")
        with mock.patch.dict(os.environ, {"JURISYNC_CAPTCHA_API_KEY": ""}):
            with credentials.scope("office-a", {"OPENROUTER_API_KEY": "customer"}):
                self.assertEqual(image_captcha.captcha_api_key(), "customer")
            with credentials.scope("office-b"), self.assertRaisesRegex(RuntimeError, "JURISYNC_CAPTCHA_API_KEY"):
                image_captcha.read_with_model(b"img")

    def test_model_request_carries_the_picture_and_reads_the_code(self):
        answer = mock.Mock()
        answer.json.return_value = {"choices": [{"message": {"content": "q3RR"}}]}
        with mock.patch("curl_cffi.requests.post", return_value=answer) as post:
            code = image_captcha.read_with_model(png([(0, 0, 0)]), api_key="k", model="m")
        self.assertEqual(code, "q3RR")
        body = post.call_args.kwargs["json"]
        self.assertEqual(body["model"], "m")
        self.assertTrue(body["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,"))
        self.assertEqual(post.call_args.kwargs["headers"], {"Authorization": "Bearer k"})


if __name__ == "__main__":
    unittest.main()
