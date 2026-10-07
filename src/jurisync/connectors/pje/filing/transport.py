"""HTTP conversation with the TRF5 PJe draft pages: requests, multipart uploads, JSF postbacks."""

import uuid
from urllib.parse import urlencode, urlsplit

from bs4 import BeautifulSoup
from curl_cffi.const import CurlHttpVersion

from jurisync.connectors.pje.filing.constants import PORTAL, UPDATE_URL
from jurisync.connectors.pje.filing.names import upload_name
from jurisync.connectors.pje.parsers import response_html


class Transport:
    def __init__(self, session):
        self.session = session
        self.referer = None
        self.requests = 0

    def get(self, url):
        self.requests += 1
        response = self.session.get(url, impersonate="firefox147", allow_redirects=False, timeout=30)
        self.referer = url
        return self._html(response)

    def post(self, url, data):
        self.requests += 1
        response = self.session.post(
            url,
            data=urlencode(data),
            headers={
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "Accept": "*/*",
                "Origin": f"https://{PORTAL.host}",
                "Referer": self.referer or url,
            },
            impersonate="firefox147",
            allow_redirects=False,
            timeout=30,
        )
        return self._html(response)

    def multipart(self, url, data, field, path, *, resource=False):
        self.requests += 1
        boundary = "----geckoformboundary" + uuid.uuid4().hex
        body = bytearray()

        def part(name, value, filename=None):
            if any(char in name for char in '\r\n"') or filename and any(char in filename for char in '\r\n"'):
                raise ValueError("Invalid multipart field or filename")
            body.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"'.encode())
            if filename:
                body.extend(f'; filename="{filename}"\r\nContent-Type: application/pdf'.encode())
            body.extend(b"\r\n\r\n")
            body.extend(value)
            body.extend(b"\r\n")

        for name, value in data.items():
            if name == "javax.faces.ViewState" and not resource:
                part(field, path.read_bytes(), upload_name(path))
            part(name, str(value).encode("utf-8" if resource else "iso-8859-1"))
        if resource or "javax.faces.ViewState" not in data:
            part(field, path.read_bytes(), upload_name(path))
        body.extend(f"--{boundary}--\r\n".encode())
        response = self.session.post(
            url,
            data=bytes(body),
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Accept": "application/json"
                if resource
                else "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Origin": f"https://{PORTAL.host}",
                "Referer": self.referer or UPDATE_URL,
                "Sec-Fetch-Dest": "empty" if resource else "iframe",
                "Sec-Fetch-Mode": "cors" if resource else "navigate",
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:147.0) Gecko/20100101 Firefox/147.0",
                **({"Sec-Fetch-User": "?1"} if not resource else {}),
                **({"X-Requested-With": "XMLHttpRequest"} if resource else {}),
                "Sec-Fetch-Site": "same-origin",
            },
            impersonate="firefox147",
            allow_redirects=False,
            timeout=60,
            http_version=CurlHttpVersion.V1_1,
        )
        if response.status_code != 200 or urlsplit(response.url).hostname != PORTAL.host:
            raise RuntimeError(f"PJe document upload returned HTTP {response.status_code}")
        return response

    def ajax(self, url, state, fields, container, action):
        return self.post(
            url,
            {"AJAXREQUEST": container, **fields, "javax.faces.ViewState": state, **action, "AJAX:EVENTS_COUNT": "1"},
        )

    @staticmethod
    def _html(response):
        if response.status_code != 200 or urlsplit(response.url).hostname != PORTAL.host:
            raise RuntimeError(f"PJe returned HTTP {response.status_code} outside the expected draft page")
        html = response_html(response)
        redirect = BeautifulSoup(html, "html.parser").find(
            "meta", attrs={"name": "Ajax-Response", "content": "redirect"}
        )
        if redirect and "errorUnexpected.seam" in html:
            raise RuntimeError("PJe redirected the draft action to errorUnexpected.seam")
        return html
