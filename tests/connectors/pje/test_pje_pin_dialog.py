"""A second PJeOffice prompt should be handled after the first closes."""

import sys
from contextlib import nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from jurisync.connectors.pje.auth import pin_dialog
from jurisync.connectors.pje.auth.setup_a1 import configure_a1


def test_configure_a1_preserves_other_settings():
    with TemporaryDirectory() as directory:
        config = Path(directory) / "pjeoffice-pro.config"
        config.write_text("list.a3=\nlist.server=preserved\nlist.a1=/old/cert.p12\n")
        configure_a1(config, Path("/new cert/cert.p12"))
        assert config.read_text() == "list.a3=\nlist.server=preserved\nlist.a1=/new cert/cert.p12\n"


def test_multiple_password_dialogs():
    clock = [1.0]
    events = []

    class Keyboard:
        def pressed(self, key):
            return nullcontext()

        def press(self, key):
            events.append(("press", key))

        def release(self, key):
            pass

        def type(self, value):
            events.append(("type", value))

    keyboard = SimpleNamespace(Controller=Keyboard, Key=SimpleNamespace(ctrl="ctrl", enter="enter"))

    def dialogs(_display):
        enters = events.count(("press", "enter"))
        return [1, 2] if enters < 2 else []

    def sleep(seconds):
        clock[0] += seconds

    with (
        patch.dict(sys.modules, {"pynput.keyboard": keyboard}),
        patch.object(pin_dialog, "dialog_ids", side_effect=dialogs),
        patch.object(pin_dialog, "focus_password_field", return_value=True),
        patch.object(pin_dialog, "focus_is_dialog", return_value=True),
        patch.object(pin_dialog.time, "monotonic", side_effect=lambda: clock[0]),
        patch.object(pin_dialog.time, "sleep", side_effect=sleep),
    ):
        assert pin_dialog.main("test", timeout=20) == 0

    assert events.count(("type", "test")) == 2
    assert events.count(("press", "enter")) == 2
