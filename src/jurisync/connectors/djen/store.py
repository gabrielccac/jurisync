"""Local DJEN poll state, keyed by communication ID and cancellation."""

import json
import os
import re
import tempfile

DEFAULT_BASE_DIR = os.path.join("data", "djen")


def state_path(base_dir, numero_oab, uf_oab, sigla_tribunal=None):
    tribunal = sigla_tribunal or "all"
    name = re.sub(r"[^A-Za-z0-9_-]", "_", f"{numero_oab}-{uf_oab}-{tribunal}") + ".json"
    return os.path.join(base_dir or DEFAULT_BASE_DIR, name)


def load_state(path):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        data = {}
    data.setdefault("ultima_data", None)
    data.setdefault("ids_vistos", [])
    data.setdefault("ids_cancelados", [])
    data.setdefault("count_total", 0)
    return data


def save_state(path, state):
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".djen-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return path


def filter_new(items, state):
    """Return new communications and first observed cancellation changes."""
    seen = set(map(str, state.get("ids_vistos", [])))
    cancelled = set(map(str, state.get("ids_cancelados", [])))
    novos, dups = [], 0
    for item in items:
        key = item.get("id") or item.get("comunicacao_id")
        if key is None:
            raise ValueError("comunicação DJEN sem ID")
        key = str(key)
        is_cancelled = bool(item.get("data_cancelamento")) or item.get("ativo") is False
        if key in seen and not (is_cancelled and key not in cancelled):
            dups += 1
        else:
            novos.append(item)
            seen.add(key)
            if is_cancelled:
                cancelled.add(key)
    return novos, dups


def mark_seen(state, items):
    seen = set(map(str, state.get("ids_vistos", [])))
    cancelled = set(map(str, state.get("ids_cancelados", [])))
    for item in items:
        key = item.get("id") or item.get("comunicacao_id")
        if key is None:
            raise ValueError("comunicação DJEN sem ID")
        seen.add(str(key))
        if item.get("data_cancelamento") or item.get("ativo") is False:
            cancelled.add(str(key))
    state["ids_vistos"] = sorted(seen)
    state["ids_cancelados"] = sorted(cancelled)
    state["count_total"] = len(state["ids_vistos"])
    return state


def stats(items):
    """Digest por órgão para o worker diário."""
    by_orgao = {}
    for item in items:
        orgao = (
            (item.get("orgao") or {}).get("nome") if isinstance(item.get("orgao"), dict) else item.get("nomeOrgao", "?")
        )
        by_orgao[orgao] = by_orgao.get(orgao, 0) + 1
    return {"total": len(items), "por_orgao": by_orgao}
