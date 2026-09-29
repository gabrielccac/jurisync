"""Estado local do poll DJEN (arquivo JSON sob data/djen/).

Mesmo padrão do watcher 1G: sem serviço/fila/banco/scheduler.
Janela incremental por ``data_disponibilizacao`` + catch-up 30 dias
na primeira carga. Idempotência por ``id`` da comunicação.
"""

import json
import os

DEFAULT_BASE_DIR = os.path.join("data", "djen")


def state_path(base_dir, numero_oab, uf_oab, sigla_tribunal=None):
    tribunal = sigla_tribunal or "all"
    name = f"{numero_oab}-{uf_oab}-{tribunal}.json".replace("/", "_")
    return os.path.join(base_dir or DEFAULT_BASE_DIR, name)


def load_state(path):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {"ultima_data": None, "ids_vistos": [], "count_total": 0}
    data.setdefault("ultima_data", None)
    data.setdefault("ids_vistos", [])
    data.setdefault("count_total", 0)
    return data


def save_state(path, state):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return path


def filter_new(items, state):
    """Separa (novos, duplicados) por ``id``; observar data_cancelamento fora."""
    seen = set(state.get("ids_vistos", []))
    novos, dups = [], 0
    for item in items:
        key = item.get("id", item.get("comunicacao_id"))
        if key in seen:
            dups += 1
        else:
            novos.append(item)
    return novos, dups


def mark_seen(state, items):
    seen = set(state.get("ids_vistos", []))
    for item in items:
        key = item.get("id", item.get("comunicacao_id"))
        if key is not None:
            seen.add(key)
    state["ids_vistos"] = sorted(seen)
    state["count_total"] = len(state["ids_vistos"])
    return state


def stats(items):
    """Digest por órgão para o worker diário."""
    by_orgao = {}
    for item in items:
        orgao = (item.get("orgao") or {}).get("nome") if isinstance(item.get("orgao"), dict) \
            else item.get("nomeOrgao", "?")
        by_orgao[orgao] = by_orgao.get(orgao, 0) + 1
    return {"total": len(items), "por_orgao": by_orgao}
