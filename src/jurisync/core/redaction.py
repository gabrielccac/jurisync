"""Keep tokens, session hashes, raw portal URLs and CPFs out of results and logs."""

import re
from urllib.parse import urlsplit

# A CPF keeps its first three and last two digits (docs/padroes.md, section 5): formatted in free text, after a "CPF"
# label, or as a whole value (a taxId field); the last two take any of the dots and the dash. A CNPJ is a company's and
# stays whole; process numbers and OABs never match (other lengths, or a CPF label would be needed).
CPF_PATTERNS = (
    (re.compile(r"(?<![\d.])(\d{3})\.\d{3}\.\d{3}-(\d{2})(?!\d)"), r"\1.***.***-\2"),
    (re.compile(r"(\bCPF\W{0,3})(\d{3})\.?\d{3}\.?\d{3}-?(\d{2})(?!\d)", re.I), r"\1\2******\3"),
    (re.compile(r"^\s*(\d{3})\.?\d{3}\.?\d{3}-?(\d{2})\s*$"), r"\1.***.***-\2"),
)


def mask_cpf(value):
    """The text with every CPF masked as 123.***.***-45; anything else (names, CNPJs, None) is kept."""
    if not isinstance(value, str):
        return value
    for pattern, replacement in CPF_PATTERNS:
        value = pattern.sub(replacement, value)
    return value


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
