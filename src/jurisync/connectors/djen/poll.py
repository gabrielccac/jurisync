"""Run one DJEN polling window and advance local state only after a full fetch."""

from datetime import date, timedelta

from jurisync.connectors.djen.client import _check_window, fetch_all_variants
from jurisync.connectors.djen.normalize import normalize_item
from jurisync.connectors.djen.store import filter_new, load_state, mark_seen, save_state, state_path, stats


def poll_window(
    numero_oab, uf_oab, tribunal=None, inicio=None, fim=None, state_dir=None, itens_por_pagina=50, today=None
):
    path = state_path(state_dir, numero_oab, uf_oab, tribunal)
    state = load_state(path)
    end = fim or (today or date.today()).isoformat()
    start = inicio or state["ultima_data"] or (date.fromisoformat(end) - timedelta(days=29)).isoformat()
    _check_window(start, end)
    fetched = fetch_all_variants(numero_oab, uf_oab, start, end, tribunal, itens_por_pagina)
    items = [normalize_item(item) for item in fetched["items"]]
    new, duplicates = filter_new(items, state)
    mark_seen(state, new)
    state["ultima_data"] = max(state["ultima_data"] or end, end)
    save_state(path, state)
    return {
        "inicio": start,
        "fim": end,
        "count": fetched["count"],
        "unique": fetched["unique"],
        "new": len(new),
        "duplicates": duplicates,
        "items": new,
        "stats": stats(new),
    }
