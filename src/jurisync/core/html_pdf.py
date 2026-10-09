"""Render an HTML court document to PDF with headless Chrome so it can be attached as a file."""

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

CHARSET = re.compile(r"charset=([\w-]+)", re.I)


def chrome_path():
    return os.environ.get("JURISYNC_CHROME_PATH") or shutil.which("google-chrome") or shutil.which("chromium")


def html_to_pdf(content, content_type="", timeout=60):
    """Return the PDF bytes for ``content`` or ``None`` when no browser is available or it fails."""
    chrome = chrome_path()
    if not chrome:
        return None
    charset = (CHARSET.search(content_type or "") or [None, "utf-8"])[1]
    try:
        html = content.decode(charset, errors="replace")
    except LookupError:
        html = content.decode("utf-8", errors="replace")
    html = re.sub(r"<meta[^>]+charset[^>]*>", "", html, flags=re.I)
    html = '<meta charset="utf-8">' + html
    with tempfile.TemporaryDirectory() as directory:
        source, target = Path(directory) / "document.html", Path(directory) / "document.pdf"
        source.write_text(html, encoding="utf-8")
        command = [
            chrome,
            "--headless=new",
            "--disable-gpu",
            "--no-sandbox",
            "--no-pdf-header-footer",
            f"--user-data-dir={directory}/profile",
            f"--print-to-pdf={target}",
            source.as_uri(),
        ]
        try:
            subprocess.run(command, check=True, capture_output=True, timeout=timeout)
        except (subprocess.SubprocessError, OSError):
            return None
        return target.read_bytes() if target.is_file() and target.stat().st_size > 1000 else None
