"""ADVBOX customer files: listing, bounded downloads, and PDF text."""

import os
import re
from pathlib import Path, PurePosixPath
from urllib.parse import quote, unquote, urlencode, urljoin, urlsplit

from jurisync.connectors.advbox.client import positive_integer
from jurisync.connectors.advbox.parsers import BASE_URL, parse_file_tree
from jurisync.connectors.advbox.session import SessionExpiredError
from jurisync.core.pdf import analyze_document, available_path
from jurisync.paths import REPO_ROOT

MAX_FOLDERS = 100


def _max_download_bytes():
    return int(os.environ.get("ADVBOX_MAX_DOWNLOAD_BYTES") or 50 * 1024 * 1024)


def _max_text_chars():
    return int(os.environ.get("ADVBOX_MAX_PDF_TEXT_CHARS") or 500_000)


def _origin(url):
    parts = urlsplit(url)
    return parts.scheme, parts.hostname, parts.port


def is_download_url(value):
    try:
        parts = urlsplit(value)
        return _origin(value) == _origin(BASE_URL) and (
            re.match(r"^/(files|posts-file)/", parts.path) is not None or parts.path == "/file/download"
        )
    except (TypeError, ValueError):
        return False


def download_filename(content_disposition, fallback):
    match = re.search(r"""filename\*?=(?:UTF-8'')?"?([^";]+)"?""", content_disposition or "", re.I)
    name = fallback
    if match:
        name = unquote(match[1])
        if re.search(r"[\u0080-ÿ]", name):
            try:
                name = name.encode("latin-1").decode("utf-8")
            except (UnicodeEncodeError, UnicodeDecodeError):
                pass
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", os.path.basename(name)).strip()
    return name if name and name not in (".", "..") else "advbox-download"


def folder_zip_url(customer_id, folder_path):
    return f"{BASE_URL}/file/download?{urlencode({'customer': customer_id, 'path': folder_path}, quote_via=lambda value, *_: quote(value, safe="!'()*"))}"


class AdvboxDocuments:
    def __init__(self, client):
        self.session = client.session

    def list_customer_files(self, customer_id, folder=""):
        def read(session):
            html = session.with_csrf(
                lambda token: session.post_form(
                    "/file/search",
                    urlencode(
                        {
                            "root": "customer",
                            "folder": str(customer_id),
                            "search": "",
                            "path_filter": folder,
                            "order": "name,asc",
                            "_token": token,
                        }
                    ),
                    retry_429=True,
                )
            )
            tree = parse_file_tree(html)
            for entry in tree["folders"]:
                entry["zipUrl"] = folder_zip_url(customer_id, entry["path"])
            return {"customerId": str(customer_id), "folder": folder, **tree}

        return self.session.using(read)

    def _fetch_bytes(self, url, label, max_bytes):
        if not is_download_url(url):
            raise ValueError(f"ADVBOX {label} url must be an app.advbox.com.br files, posts-file, or file/download URL")
        positive_integer(max_bytes, "maxBytes", 2**53 - 1)
        current, use_cookies = url, True
        for _ in range(6):
            with self.session.stream(current, use_cookies=use_cookies) as response:
                if 300 <= response.status_code < 400:
                    location = response.headers.get("location") or ""
                    if not location:
                        raise SessionExpiredError(f"ADVBOX session expired while downloading {label}")
                    current = urljoin(current, location)
                    # Signed S3 targets carry their own auth; drop session cookies off-origin.
                    use_cookies = _origin(current) == _origin(BASE_URL)
                    continue
                if response.status_code == 401:
                    raise SessionExpiredError(f"ADVBOX session expired while downloading {label}")
                if not 200 <= response.status_code < 300:
                    raise RuntimeError(f"ADVBOX download failed ({response.status_code})")
                return read_bounded(response, label, max_bytes)
        raise RuntimeError(f"ADVBOX download exceeded redirect limit for {label}")

    def download_file(self, url, *, filename=None, directory=None, ocr=True, inspect=True):
        if not is_download_url(url):
            raise ValueError("ADVBOX download url must be an app.advbox.com.br files, posts-file, or file/download URL")

        def download(session):
            max_bytes = _max_download_bytes()
            content = self._fetch_bytes(url, "a file", max_bytes)
            if not content:
                raise RuntimeError("ADVBOX download response was empty")
            fallback = unquote(urlsplit(url).path.split("/")[-1]) or "advbox-download"
            output_name = download_filename("", filename or fallback)
            folder = Path(directory or REPO_ROOT / "downloaded_files" / "advbox")
            folder.mkdir(parents=True, exist_ok=True)
            output = available_path(folder, output_name)
            with open(output, "xb") as handle:
                handle.write(content)
            analysis = (
                analyze_document(content, output_name, max_text_chars=_max_text_chars(), ocr=ocr) if inspect else {}
            )
            return {"path": str(output), "filename": output.name, "bytes": len(content), **analysis}

        return self.session.using(download)

    def download_folder(self, customer_id, folder, *, max_files=50, max_total_bytes=200 * 1024 * 1024, ocr=True):
        positive_integer(max_files, "maxFiles", 100)
        positive_integer(max_total_bytes, "maxTotalBytes", 2**53 - 1)
        max_chars, max_file_bytes = _max_text_chars(), _max_download_bytes()

        def download(session):
            listing = self.list_customer_files(customer_id)
            if not any(item["path"] == folder for item in listing.get("folders") or []):
                raise ValueError(f"ADVBOX folder not found for customer {customer_id}: {folder or '(root)'}")
            files, total, truncated, queue, visited = [], 0, False, [folder], set()
            while queue:
                current = queue.pop(0)
                if current in visited:
                    continue
                if len(visited) >= MAX_FOLDERS:
                    truncated = True
                    break
                visited.add(current)
                tree = self.list_customer_files(customer_id, current)
                for item in tree["files"]:
                    if len(files) >= max_files or total >= max_total_bytes:
                        truncated = True
                        break
                    limit = min(max_file_bytes, max_total_bytes - total)
                    try:
                        content = self._fetch_bytes(item.get("downloadUrl") or item["url"], "file", limit)
                    except RuntimeError as error:
                        if not re.search(r"ADVBOX file exceeds the .*download limit", str(error)):
                            raise
                        truncated = True
                        break
                    total += len(content)
                    name = str(PurePosixPath(current) / item["name"])
                    files.append(
                        {
                            "name": name,
                            "bytes": len(content),
                            **analyze_document(content, name, max_text_chars=max_chars, ocr=ocr),
                        }
                    )
                if truncated:
                    break
                queue += [child["path"] for child in tree["folders"] if child["path"] not in visited]
            with_text = [file for file in files if file.get("textCharacters") is not None]
            return {
                "customerId": str(customer_id),
                "folder": folder,
                "source": "files",
                "truncated": truncated,
                "files": files,
                "totals": {
                    "files": len(files),
                    "bytes": sum(file["bytes"] for file in files),
                    "pages": sum(file.get("pageCount") or 0 for file in files),
                    "textCharacters": sum(file.get("textCharacters") or 0 for file in with_text),
                    "filesNeedingOcr": sum(
                        1 for file in files if file.get("pagesRecommendedForOcr") or file.get("pagesNeedingOcr")
                    ),
                    "filesOcred": sum(1 for file in files if file.get("pagesRoutedToOcr")),
                },
            }

        return self.session.using(download)


def read_bounded(response, label, max_bytes):
    length = response.headers.get("content-length")
    if length and int(length) > max_bytes:
        raise RuntimeError(f"ADVBOX {label} exceeds the {max_bytes}-byte download limit")
    chunks, size = [], 0
    for chunk in response.iter_content():
        size += len(chunk)
        if size > max_bytes:
            raise RuntimeError(f"ADVBOX {label} exceeds the {max_bytes}-byte download limit")
        chunks.append(chunk)
    return b"".join(chunks)
