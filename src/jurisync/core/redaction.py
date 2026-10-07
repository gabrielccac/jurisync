"""Keep tokens, session hashes, and raw portal URLs out of results and logs."""

from urllib.parse import urlsplit


def url_shape(value):
    """Return only scheme://host/path; queries carry codes, tickets, and session hashes."""
    parts = urlsplit(value or "")
    return f"{parts.scheme.lower()}://{(parts.hostname or '').lower()}{parts.path}"


def drop_keys(value, keys):
    """Recursively remove keys (case-insensitive) from nested dicts and lists."""
    if isinstance(value, list):
        return [drop_keys(item, keys) for item in value]
    if isinstance(value, dict):
        return {key: drop_keys(item, keys) for key, item in value.items() if key.lower() not in keys}
    return value
