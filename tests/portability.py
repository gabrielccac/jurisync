"""What the system running the tests supports, for the few checks that only hold on some systems."""

import os
import stat
import tempfile
from pathlib import Path


def assert_mode(case, path, mode):
    """Owner-only permission bits. Windows has none (chmod only toggles read-only, 0o600 reads back as 0o666), so
    there the check is skipped."""
    if os.name != "nt":
        case.assertEqual(stat.S_IMODE(os.stat(path).st_mode), mode, path)


def _can_symlink():
    with tempfile.TemporaryDirectory() as directory:
        try:
            (Path(directory) / "link").symlink_to(directory, target_is_directory=True)
        except OSError:
            return False
    return True


SYMLINKS = _can_symlink()
NO_SYMLINKS = "Windows creates symlinks only with Developer Mode or administrator rights"
