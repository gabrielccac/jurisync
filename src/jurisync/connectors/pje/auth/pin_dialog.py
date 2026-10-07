#!/usr/bin/env python3
"""Enter a PJeOffice password in each visible "Informe a senha" dialog.

Exit 0 means Enter was sent and the dialogs closed; it does not verify login.
"""

import argparse
import os
import re
import subprocess
import sys
import time

from Xlib import display as xdisplay
from Xlib.error import BadWindow


def dialog_ids(display):
    out = subprocess.run(
        ["xwininfo", "-root", "-tree", "-display", display],
        capture_output=True,
        text=True,
        check=True,
        timeout=15,
    ).stdout
    ids = [int(w, 16) for w in re.findall(r'(?m)^\s*(0x[0-9a-f]+) "Informe a senha"', out)]
    if not ids:
        return []
    d = xdisplay.Display(display)
    try:
        prop = d.screen().root.get_full_property(d.intern_atom("_NET_CLIENT_LIST_STACKING"), 0)
        stacking = list(prop.value) if prop else []
        return sorted(ids, key=lambda wid: stacking.index(wid) if wid in stacking else -1, reverse=True)
    finally:
        d.close()


def focus_is_dialog(display, wid):
    d = xdisplay.Display(display)
    try:
        win = d.create_resource_object("window", wid)
        focused = d.get_input_focus().focus
        focused_id = getattr(focused, "id", None)
        return focused_id == wid or focused_id in {child.id for child in win.query_tree().children}
    except BadWindow:
        return False
    finally:
        d.close()


def focus_password_field(display, wid):
    from pynput.mouse import Button, Controller

    d = xdisplay.Display(display)
    try:
        win = d.create_resource_object("window", wid)
        geometry = win.get_geometry()
        position = d.screen().root.translate_coords(win, 0, 0)
        win.set_input_focus(1, 0)
        d.sync()
        mouse = Controller()
        # ponytail: PJeOffice 2.5 layout; use accessibility lookup if it changes.
        mouse.position = (position.x + geometry.width // 2, position.y + geometry.height * 3 // 5)
        mouse.click(Button.left)
        time.sleep(0.2)
        return focus_is_dialog(display, wid)
    except BadWindow:
        return False
    finally:
        d.close()


def main(password, display=":1", timeout=90):
    os.environ["DISPLAY"] = display
    from pynput.keyboard import Controller, Key

    keyboard = Controller()
    handled = set()
    deadline = time.monotonic() + timeout
    idle_since = None
    while time.monotonic() < deadline:
        ids = dialog_ids(display)
        pending = [wid for wid in ids if wid not in handled]
        if pending:
            idle_since = None
            wid = pending[0]
            if not focus_password_field(display, wid):
                time.sleep(0.5)
                continue
            with keyboard.pressed(Key.ctrl):
                keyboard.press("a")
                keyboard.release("a")
            keyboard.type(password)
            if not focus_is_dialog(display, wid):
                continue
            keyboard.press(Key.enter)
            keyboard.release(Key.enter)
            handled.add(wid)
            print(f"Submitted password dialog {hex(wid)}")
        elif handled and not ids:
            idle_since = idle_since or time.monotonic()
            if time.monotonic() - idle_since >= 10:
                return 0
        time.sleep(0.5)
    print("Timed out waiting for password dialogs to close", file=sys.stderr)
    return 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("password", nargs="?")
    parser.add_argument("--password-stdin", action="store_true")
    parser.add_argument("--display", default=":1")
    parser.add_argument("--timeout", type=int, default=90)
    args = parser.parse_args()
    if args.password_stdin:
        args.password = sys.stdin.readline().rstrip("\r\n")
    if args.password is None:
        parser.error("provide password or --password-stdin")
    sys.exit(main(args.password, args.display, args.timeout))
