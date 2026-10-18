"""Google Drive API v3 calls the exporter needs: folder metadata, paginated listing, download and PDF export.

`http(method, url, headers=..., data=None, sink=None, limit=None)` returns (status, headers, body). With a `sink`
(a binary file) the body is streamed into it instead, at most `limit` bytes. The default goes through `urllib` (no
extra dependency); tests pass their own.
"""

import hashlib
import json
import urllib.error
import urllib.request
from urllib.parse import quote, urlencode

API = "https://www.googleapis.com/drive/v3/files"
FOLDER = "application/vnd.google-apps.folder"
# Google Docs, Sheets, Slides and Drawings have no bytes of their own: they are exported as PDF.
EXPORTABLE = {
    "application/vnd.google-apps.document",
    "application/vnd.google-apps.spreadsheet",
    "application/vnd.google-apps.presentation",
    "application/vnd.google-apps.drawing",
}
FIELDS = "id,name,mimeType,size,md5Checksum,modifiedTime,webViewLink,driveId,parents"
COMMON = {"supportsAllDrives": "true"}


class DriveError(RuntimeError):
    pass


class TooLarge(DriveError):
    pass


def urllib_http(method, url, headers=None, data=None, sink=None, limit=None):
    request = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        response = urllib.request.urlopen(request, timeout=120)
    except urllib.error.HTTPError as error:
        with error:
            return error.code, dict(error.headers), error.read(4096)
    with response:
        if sink is None:
            return response.status, dict(response.headers), response.read()
        total = 0
        while chunk := response.read(1 << 20):
            total += len(chunk)
            if limit is not None and total > limit:
                raise TooLarge(f"more than {limit} bytes")
            sink.write(chunk)
        return response.status, dict(response.headers), b""


class DriveClient:
    def __init__(self, account, http=urllib_http):
        self.account = account
        self.http = http

    def _get(self, url, *, sink=None, limit=None):
        headers = {"Authorization": f"Bearer {self.account.token()}"}
        return self.http("GET", url, headers=headers, sink=sink, limit=limit)

    def _json(self, url, what):
        status, _, body = self._get(url)
        if status != 200:
            raise self._error(status, body, what)
        return json.loads(body)

    def _error(self, status, body, what):
        detail = body[:200].decode("utf-8", "replace") if body else ""
        if status in (403, 404):
            return DriveError(
                f"Google Drive answered {status} for {what}: it does not exist or is not shared with the service "
                f"account {self.account.email}. Share it with that address as Viewer (Leitor)"
            )
        return DriveError(f"Google Drive answered {status} for {what}: {detail}")

    def metadata(self, file_id):
        return self._json(f"{API}/{quote(file_id)}?{urlencode({'fields': FIELDS, **COMMON})}", f"item {file_id}")

    def children(self, folder_id):
        """Every non-trashed item directly inside the folder, all pages."""
        items, token = [], None
        while True:
            query = {
                "q": f"'{folder_id}' in parents and trashed = false",
                "fields": f"nextPageToken,files({FIELDS})",
                "pageSize": "1000",
                "includeItemsFromAllDrives": "true",
                "orderBy": "name",
                **COMMON,
            }
            if token:
                query["pageToken"] = token
            page = self._json(f"{API}?{urlencode(query)}", f"folder {folder_id}")
            items += page.get("files", [])
            token = page.get("nextPageToken")
            if not token:
                return items

    def download(self, item, sink, limit):
        """Stream a file's bytes (or a Google Doc's PDF export) into `sink`; returns the md5 of what was written."""
        if item["mimeType"] in EXPORTABLE:
            url = f"{API}/{quote(item['id'])}/export?{urlencode({'mimeType': 'application/pdf'})}"
        else:
            url = f"{API}/{quote(item['id'])}?{urlencode({'alt': 'media', **COMMON})}"
        digest = _Md5Sink(sink)
        try:
            status, _, body = self._get(url, sink=digest, limit=limit)
        except TooLarge:
            raise TooLarge(f"{item['name']} is larger than the {limit // 1_000_000} MB limit") from None
        if status != 200:
            raise self._error(status, body, f"file {item['name']}")
        return digest.hexdigest()


class _Md5Sink:
    def __init__(self, sink):
        self.sink, self.md5 = sink, hashlib.md5(usedforsecurity=False)

    def write(self, chunk):
        self.md5.update(chunk)
        self.sink.write(chunk)

    def hexdigest(self):
        return self.md5.hexdigest()
