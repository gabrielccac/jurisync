"""DJEN (Comunica/CNJ). What other connectors may use of it is exported here: the STJ reads its notices to lawyers,
which the STJ publishes only in the DJEN."""

from jurisync.connectors.djen.client import fetch_page
from jurisync.connectors.djen.normalize import normalize_item

__all__ = ["fetch_page", "normalize_item"]
