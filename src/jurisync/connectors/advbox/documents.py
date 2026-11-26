"""ADVBOX customer files: listing, bounded downloads, and PDF text."""

import os
import re
from pathlib import Path, PurePosixPath
from urllib.parse import parse_qs, quote, unquote, urlencode, urljoin, urlsplit

from curl_cffi.requests.exceptions import RequestException

from jurisync.connectors.advbox.client import UnconfirmedWriteError, _plain, positive_integer
from jurisync.connectors.advbox.parsers import BASE_URL, parse_file_tree, parse_uploaded_file
from jurisync.connectors.advbox.session import SessionExpiredError, require_writes
from jurisync.core import credentials
from jurisync.core.documents_dir import FOLDER_FILE_PATTERN, save_folder_file
from jurisync.core.pdf import analyze_document, available_path, ocr_deadline
from jurisync.paths import STATE_DIR

MAX_FOLDERS = 100
# The screen's rename box for a folder takes at most 40 characters.
MAX_FOLDER_NAME = 40
MAX_UPLOAD_FILES = 10


def _unsure(error):
    """True when a write may have happened although it failed: no answer, or a 5xx after the request was sent."""
    return isinstance(error, RequestException) or bool(re.search(r"failed \(5\d\d\)", str(error)))


def folder_name(name):
    """A folder name ADVBOX's screen would accept: one level (no slash), 1 to 40 characters, not a dot name."""
    name = re.sub(r"\s+", " ", str(name or "")).strip()
    if not name or len(name) > MAX_FOLDER_NAME:
        raise ValueError(f"ADVBOX folder names have 1 to {MAX_FOLDER_NAME} characters")
    if re.search(r"[/\\\x00-\x1f]", name) or name in (".", "..") or _traverses(name):
        raise ValueError("ADVBOX folder name must be one level: no slash, backslash, control character or '..'")
    return name


def folder_path(path):
    """A customer folder path as listed by advbox_list_files ('' is the root); parent segments are refused."""
    path = str(path or "").strip().strip("/")
    if path and (_traverses(path) or "\\" in path or re.search(r"[\x00-\x1f]", path) or "//" in path):
        raise ValueError("ADVBOX folder path must be a path listed by advbox_list_files, without '..'")
    return path


def _same_path(left, right):
    return left.casefold() == right.casefold()


def _max_download_bytes():
    return int(os.environ.get("ADVBOX_MAX_DOWNLOAD_BYTES") or 50 * 1024 * 1024)


def _max_text_chars():
    return int(os.environ.get("ADVBOX_MAX_PDF_TEXT_CHARS") or 500_000)


def _origin(url):
    parts = urlsplit(url)
    return parts.scheme, parts.hostname, parts.port


def _traverses(text):
    """True when a path names a parent folder, plainly or percent-encoded (once or twice)."""
    decoded = unquote(text)
    return "%2e" in text.lower() or "%2e" in decoded.lower() or ".." in decoded.replace("\\", "/").split("/")


def is_download_url(value):
    try:
        parts = urlsplit(value)
        if _origin(value) != _origin(BASE_URL) or _traverses(parts.path):
            return False
        if parts.path == "/file/download":
            return not any(_traverses(path) for path in parse_qs(parts.query).get("path", []))
        return re.match(r"^/(files|posts-file)/", parts.path) is not None
    except (TypeError, ValueError):
        return False


def saved_folder_name(name):
    """A file name the shared documents folder accepts, kept as close to the source name as it allows: letters
    (accents included), digits, space and ``.,()-_``, starting with a letter or digit, at most 181 characters."""
    name = download_filename("", name)
    if FOLDER_FILE_PATTERN.fullmatch(name):
        return name
    stem, dot, suffix = name.rpartition(".")
    if not dot or not re.fullmatch(r"\w{1,10}", suffix):
        stem, suffix = name, ""
    stem = re.sub(r"^[\W_]+", "", re.sub(r"[^\w .,()\-]", "_", stem))
    suffix = f".{suffix}" if suffix else ""
    safe = (stem[: 181 - len(suffix)].rstrip() or "arquivo") + suffix
    return safe if FOLDER_FILE_PATTERN.fullmatch(safe) else "arquivo" + suffix


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
        self._client = client

    @property
    def session(self):
        """The client's session, resolved on every call: after an office's credentials change its client is
        replaced, and the file tools must follow it instead of keeping the previous account's cookies."""
        return self._client.session

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

    # -- folder writes (require ADVBOX_ALLOW_WRITES=true) --------------------

    def find_folder(self, customer_id, path):
        """The listed entry of the customer folder ``path`` (read from its parent's listing), or None. A nested
        listing may name its folders by full path or by name; both are accepted. The match ignores case."""
        path = folder_path(path)
        if not path:
            return {"path": "", "name": "", "uploadable": True}
        parent = path.rpartition("/")[0]
        for entry in self.list_customer_files(customer_id, parent)["folders"]:
            listed = entry["path"].strip("/")
            full = f"{parent}/{listed}" if parent and not listed.startswith(f"{parent}/") else listed
            if _same_path(full, path):
                return {**entry, "path": full}
        return None

    def create_folder(self, customer_id, name, *, dry_run=False):
        """Create the folder ``name`` in the customer's files.

        ADVBOX folders are flat: a ``parent/name`` path is created as one root folder with a slash in its name
        (checked live 2026-11-09), so there are no subfolders. Idempotent: a folder of the same name (ignoring case)
        is returned with ``existing: true`` and nothing is sent. The check and the creation run under the office lock.
        """
        target = folder_name(name)
        form = {"path": target, "customer": str(customer_id)}
        base = {"customerId": str(customer_id), "name": target, "path": target}

        def existing():
            return self.find_folder(customer_id, target)

        if dry_run:
            found = existing()
            return {
                **base,
                "dryRun": True,
                "existing": bool(found),
                "folder": found,
                "request": None if found else {"method": "POST", "path": "/file/create-folder", "form": form},
            }
        require_writes()
        with self._client._exclusive():
            found = existing()
            if found:
                return {**base, "created": False, "existing": True, "folder": found}
            try:
                answer = self.session.post("/file/create-folder", form)
            except (RuntimeError, ValueError, OSError, RequestException) as error:
                if not _unsure(error):
                    raise RuntimeError(f"ADVBOX did not create the folder {target}: {_plain(error)}") from error
                # A read, not a replay: the folder may exist although the answer was lost.
                try:
                    found = self.find_folder(customer_id, target)
                except (RuntimeError, ValueError, OSError, RequestException):
                    found = None
                if found:
                    return {**base, "created": True, "existing": False, "folder": found}
                raise UnconfirmedWriteError(
                    f"ADVBOX did not confirm the request: the folder {target} MAY have been created. List the "
                    "customer's files (advbox_list_files) before retrying; a new advbox_create_folder call reuses a "
                    f"folder of the same name instead of creating a second one. Cause: "
                    f"{_plain(error) or type(error).__name__}."
                ) from error
            if str(answer).strip() != "success":
                raise RuntimeError(f"ADVBOX did not create the folder {target}: {_plain(answer) or 'empty answer'}")
            found = self.find_folder(customer_id, target)
        result = {**base, "created": True, "existing": False, "folder": found or {"path": target, "name": target}}
        if not found:
            result["warnings"] = [
                "ADVBOX answered success but the folder is not listed yet; list the customer's files before uploading."
            ]
        return result

    def _upload_target(self, customer_id, folder):
        found = self.find_folder(customer_id, folder)
        if found is None:
            raise ValueError(
                f"ADVBOX folder not found for customer {customer_id}: {folder} (create it with advbox_create_folder)"
            )
        if not found.get("uploadable", True):
            raise ValueError(f"ADVBOX folder {found['path']} is a system folder; files cannot be uploaded into it")
        return found

    def upload_files(self, customer_id, folder, files, *, dry_run=False):
        """Upload ``files`` (dicts with filename, bytes, contentType) into an existing customer folder.

        ADVBOX stores each file in one request (``POST /s3`` with root=customer, folder=<customer id>,
        path_filter=<folder path>) and answers with the stored entry. All-or-nothing on a clear refusal: the files
        this call already stored are deleted again (the same ``/s3`` DELETE the task attachments use). On a failure
        that leaves the outcome unknown (no answer, a 5xx, an answer without the stored file) nothing is replayed or
        deleted, and the error says which file MAY have been uploaded.
        """
        folder = folder_path(folder)
        if not folder:
            raise ValueError("Choose the folder to upload into (a path listed by advbox_list_files)")
        if not files or len(files) > MAX_UPLOAD_FILES:
            raise ValueError(f"Upload 1 to {MAX_UPLOAD_FILES} files per call")
        plan = [
            {"filename": file["filename"], "bytes": len(file["bytes"]), "contentType": file.get("contentType")}
            for file in files
        ]
        base = {"customerId": str(customer_id), "folder": folder}
        if dry_run:
            target = self._upload_target(customer_id, folder)
            return {
                **base,
                "dryRun": True,
                "target": target,
                "files": plan,
                "request": {
                    "method": "POST",
                    "path": "/s3",
                    "multipart": {
                        "file[]": "<one file per request>",
                        "root": "customer",
                        "folder": str(customer_id),
                        "path_filter": target["path"],
                    },
                },
            }
        require_writes()
        uploaded = []
        with self._client._exclusive():
            folder = self._upload_target(customer_id, folder)["path"]
            for index, file in enumerate(files):
                try:
                    answer = self.session.post_multipart(
                        "/s3",
                        {
                            "file[]": {
                                "bytes": file["bytes"],
                                "filename": file["filename"],
                                "contentType": file.get("contentType") or "application/octet-stream",
                            },
                            "root": "customer",
                            "folder": str(customer_id),
                            "path_filter": folder,
                        },
                    )
                except (RuntimeError, ValueError, OSError, RequestException) as error:
                    cause = _plain(error) or type(error).__name__
                    if _unsure(error):
                        raise UnconfirmedWriteError(
                            self._unconfirmed(folder, file, uploaded, files[index + 1 :], cause)
                        ) from error
                    raise RuntimeError(
                        f"ADVBOX upload of {file['filename']} into {folder} failed ({cause}).{self._rollback(uploaded)}"
                    ) from error
                entry = parse_uploaded_file(answer)
                if entry is None:
                    raise UnconfirmedWriteError(
                        self._unconfirmed(
                            folder, file, uploaded, files[index + 1 :], "ADVBOX's answer did not name the stored file"
                        )
                    )
                uploaded.append(
                    {
                        "filename": file["filename"],
                        "storedName": entry["name"],
                        "fileId": entry["fileId"],
                        "bytes": len(file["bytes"]),
                        "url": entry["url"],
                    }
                )
        warnings = []
        try:
            listed = {item.get("fileId") for item in self.list_customer_files(customer_id, folder)["files"]}
            for item in uploaded:
                item["listed"] = item["fileId"] in listed
            missing = [item["filename"] for item in uploaded if not item["listed"]]
            if missing:
                warnings.append(f"Uploaded but not listed in {folder} yet: {', '.join(missing)}.")
        except (RuntimeError, ValueError, OSError, RequestException) as error:
            warnings.append(f"The upload succeeded but the folder could not be listed to confirm it ({_plain(error)}).")
        return {
            **base,
            "folder": folder,
            "uploaded": len(uploaded),
            "files": uploaded,
            "totals": {"files": len(uploaded), "bytes": sum(item["bytes"] for item in uploaded)},
            "warnings": warnings,
        }

    def _rollback(self, uploaded):
        """Delete the files this call stored before a later file was refused; describe the outcome."""
        if not uploaded:
            return " Nothing was uploaded."
        left = set(self._client.discard_staged([{"code": item["fileId"]} for item in uploaded]))
        if left:
            names = ", ".join(
                f"{item['filename']} (fileId {item['fileId']})" for item in uploaded if item["fileId"] in left
            )
            return f" Files uploaded earlier in this call could not be removed and are still in the folder: {names}."
        return f" The {len(uploaded)} file(s) uploaded earlier in this call were removed; the folder is as before."

    @staticmethod
    def _unconfirmed(folder, file, uploaded, rest, cause):
        done = ", ".join(f"{item['filename']} (fileId {item['fileId']})" for item in uploaded) or "none"
        pending = ", ".join(item["filename"] for item in rest) or "none"
        return (
            f"ADVBOX did not confirm the upload of {file['filename']}: it MAY have been uploaded to {folder}. List the "
            "folder (advbox_list_files) before retrying and send only the files missing there; do not repeat the call "
            f"without checking. Uploaded and confirmed: {done}. Not sent: {pending}. Cause: {cause}."
        )

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
            tenant = credentials.tenant()
            default = STATE_DIR / "downloaded_files" / "advbox"
            folder = Path(directory or (default / "tenants" / tenant if tenant else default))
            folder.mkdir(parents=True, exist_ok=True)
            output = available_path(folder, output_name)
            with open(output, "xb") as handle:
                handle.write(content)
            analysis = (
                analyze_document(content, output_name, max_text_chars=_max_text_chars(), ocr=ocr) if inspect else {}
            )
            return {"path": str(output), "filename": output.name, "bytes": len(content), **analysis}

        return self.session.using(download)

    def _walk_folder(self, customer_id, folder, max_files, max_total_bytes, handle):
        """Download the files of a customer folder (and nested ones) within the limits, calling
        ``handle(path_in_advbox, name, content)`` for each. Returns True when a limit cut the walk short."""
        listing = self.list_customer_files(customer_id)
        if not any(item["path"] == folder for item in listing.get("folders") or []):
            raise ValueError(f"ADVBOX folder not found for customer {customer_id}: {folder or '(root)'}")
        max_file_bytes = _max_download_bytes()
        count, total, truncated, queue, visited = 0, 0, False, [folder], set()
        while queue:
            current = queue.pop(0)
            if current in visited:
                continue
            if len(visited) >= MAX_FOLDERS:
                return True
            visited.add(current)
            tree = self.list_customer_files(customer_id, current)
            for item in tree["files"]:
                if count >= max_files or total >= max_total_bytes:
                    return True
                limit = min(max_file_bytes, max_total_bytes - total)
                try:
                    content = self._fetch_bytes(item.get("downloadUrl") or item["url"], "file", limit)
                except RuntimeError as error:
                    if not re.search(r"ADVBOX file exceeds the .*download limit", str(error)):
                        raise
                    return True
                total += len(content)
                count += 1
                handle(str(PurePosixPath(current) / item["name"]), item["name"], content)
            queue += [child["path"] for child in tree["folders"] if child["path"] not in visited]
        return truncated

    def download_folder(self, customer_id, folder, *, max_files=50, max_total_bytes=200 * 1024 * 1024, ocr=True):
        positive_integer(max_files, "maxFiles", 100)
        positive_integer(max_total_bytes, "maxTotalBytes", 2**53 - 1)
        max_chars = _max_text_chars()

        def download(session):
            files = []
            deadline = ocr_deadline()

            def parse(name, _file_name, content):
                files.append(
                    {
                        "name": name,
                        "bytes": len(content),
                        **analyze_document(content, name, max_text_chars=max_chars, ocr=ocr, ocr_deadline=deadline),
                    }
                )

            truncated = self._walk_folder(customer_id, folder, max_files, max_total_bytes, parse)
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

    def save_folder(self, customer_id, folder, into, *, max_files=50, max_total_bytes=200 * 1024 * 1024):
        """Save the files of a customer folder, unparsed, into the shared documents folder ``into``.

        Any workflow that needs a set of documents (a filing, a review) reads them from there, whichever system
        they came from.
        """
        positive_integer(max_files, "maxFiles", 100)
        positive_integer(max_total_bytes, "maxTotalBytes", 2**53 - 1)

        def download(session):
            saved = []

            def save(_path, name, content):
                safe = saved_folder_name(name)
                entry = {"name": save_folder_file(into, safe, content), "bytes": len(content)}
                if safe != name:
                    entry["renamedFrom"] = name
                saved.append(entry)

            truncated = self._walk_folder(customer_id, folder, max_files, max_total_bytes, save)
            return {
                "customerId": str(customer_id),
                "folder": folder,
                "savedInto": into,
                "truncated": truncated,
                "files": saved,
                "totals": {"files": len(saved), "bytes": sum(file["bytes"] for file in saved)},
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
