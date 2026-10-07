"""Request guards and conventions shared by every portal connector.

Every URL a connector follows (redirects, form actions, links found in HTML)
goes through ``require_url`` first, so a portal page can never steer an
authenticated session to another host.
"""

import logging
from urllib.parse import urlsplit

from jurisync.core.redaction import url_shape

IMPERSONATE = "chrome"  # TLS/HTTP fingerprint presented to portals behind WAFs
TIMEOUT = 30

log = logging.getLogger("jurisync.http")


class UnsafeUrlError(RuntimeError):
    pass


def require_url(value, *, hosts, path=None, path_prefix=None, label="portal", allow_fragment=True):
    """Accept only https on an allowed host and default port, without credentials.

    ``path`` requires an exact path; ``path_prefix`` a path prefix.
    Returns the URL unchanged.
    """
    parts = urlsplit(value or "")
    try:
        port = parts.port
    except ValueError:
        raise UnsafeUrlError(f"Unexpected {label} URL port") from None
    host = (parts.hostname or "").lower()
    if (
        parts.scheme.lower() != "https"
        or host not in hosts
        or port not in (None, 443)
        or parts.username
        or parts.password
        or ";" in parts.path
        or (parts.fragment and not allow_fragment)
    ):
        raise UnsafeUrlError(f"Unexpected {label} URL: {url_shape(value)}")
    if (path is not None and parts.path != path) or (
        path_prefix is not None and not parts.path.startswith(path_prefix)
    ):
        raise UnsafeUrlError(f"Unexpected {label} URL path: {url_shape(value)}")
    return value


def same_endpoint(response_url, expected_url):
    """Whether a response landed on the expected https host and path (ignoring the query)."""
    actual, expected = urlsplit(response_url or ""), urlsplit(expected_url)
    return (actual.scheme, actual.netloc, actual.path) == ("https", expected.netloc, expected.path)


def enable_request_logging():
    """Show request progress on stderr (CLIs and MCP servers; stdout stays clean)."""
    import sys

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger("jurisync")
    if not logger.handlers:
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)


def log_request(method, url, status):
    log.info("%-4s %s -> %s", method, url_shape(url), status)
