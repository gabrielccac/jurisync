"""DJEN / Comunica PJe API client (public, no auth).

Fonte primária de intimações: ``https://comunica.pje.jus.br/`` via
``https://comunicaapi.pje.jus.br/api/v1/comunicacao``.
Sem autenticação. OAB/UF/tribunal/janela sempre via parâmetro —
nunca hardcoded (pertencem ao perfil do cliente).

Regras confirmadas em produção (cf. docs/djen-comunica-trf5-guia.md):
- ``numeroOab`` é match exato de string: consultar as 7 variantes
  ("", -A, -B, -E, -N, -O, -S).
- ``itensPorPagina=50`` default; teto observado ~500, 1000 quebra com
  ``500 "sistema muito ocupado"``. Janelas curtas (1-7 dias).
- 500/429: retry limitado com backoff, depois fila morta (sem retry infinito).
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import redirect_stdout

BASE_URL = "https://comunicaapi.pje.jus.br/api/v1/comunicacao"
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 500
OAB_VARIANTS = ("", "-A", "-B", "-E", "-N", "-O", "-S")
REQUEST_TIMEOUT = 30
MAX_RETRIES = 2


class DjenError(RuntimeError):
    """Erro final após retries: vai para fila morta, nunca retry infinito."""


def build_url(numero_oab, uf_oab, data_inicio, data_fim, sigla_tribunal=None,
              pagina=1, itens_por_pagina=DEFAULT_PAGE_SIZE):
    params = {
        "numeroOab": str(numero_oab),
        "ufOab": uf_oab,
        "dataDisponibilizacaoInicio": data_inicio,
        "dataDisponibilizacaoFim": data_fim,
        "pagina": int(pagina),
        "itensPorPagina": int(itens_por_pagina),
    }
    if sigla_tribunal:
        params["siglaTribunal"] = sigla_tribunal
    return BASE_URL + "?" + urllib.parse.urlencode(params)


def _read_json(url, timeout=REQUEST_TIMEOUT):
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.load(resp), resp.status
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        return {"status": "error", "http_status": exc.code, "message": body[:300]}, exc.code


def fetch_page(numero_oab, uf_oab, data_inicio, data_fim, sigla_tribunal=None,
               pagina=1, itens_por_pagina=DEFAULT_PAGE_SIZE, timeout=REQUEST_TIMEOUT):
    """Uma página. Retorna dict com count/items. Levanta DjenError após retries."""
    if not str(numero_oab).strip() or not str(uf_oab).strip():
        raise ValueError("numero_oab e uf_oab são obrigatórios")
    if int(itens_por_pagina) < 1 or int(itens_por_pagina) > MAX_PAGE_SIZE:
        raise ValueError(f"itens_por_pagina deve estar entre 1 e {MAX_PAGE_SIZE}")
    url = build_url(numero_oab, uf_oab, data_inicio, data_fim, sigla_tribunal,
                    pagina, itens_por_pagina)
    last = None
    for attempt in range(MAX_RETRIES + 1):
        data, status = _read_json(url, timeout)
        if status == 200 and isinstance(data, dict) and data.get("status") != "error":
            data.setdefault("count", len(data.get("items", [])))
            data.setdefault("items", [])
            return data
        last = (status, data)
        if attempt < MAX_RETRIES:
            time.sleep(2 ** attempt)
    raise DjenError(f"DJEN {url} falhou após {MAX_RETRIES + 1} tentativas: {last}")


def fetch_all(numero_oab, uf_oab, data_inicio, data_fim, sigla_tribunal=None,
              itens_por_pagina=DEFAULT_PAGE_SIZE, timeout=REQUEST_TIMEOUT):
    """Todas as páginas da janela. Retorna (count, items)."""
    first = fetch_page(numero_oab, uf_oab, data_inicio, data_fim, sigla_tribunal,
                       1, itens_por_pagina, timeout)
    count = int(first.get("count", 0))
    items = list(first.get("items", []))
    pagina = 2
    while len(items) < count:
        page = fetch_page(numero_oab, uf_oab, data_inicio, data_fim, sigla_tribunal,
                          pagina, itens_por_pagina, timeout)
        batch = page.get("items", [])
        if not batch:
            break
        items.extend(batch)
        pagina += 1
    return count, items


def fetch_all_variants(numero_base, uf_oab, data_inicio, data_fim, sigla_tribunal=None,
                       itens_por_pagina=DEFAULT_PAGE_SIZE, timeout=REQUEST_TIMEOUT):
    """Busca as 7 variantes de OAB e agrega sem duplicar (id = id da comunicação)."""
    seen = {}
    total = 0
    for variant in OAB_VARIANTS:
        count, items = fetch_all(f"{numero_base}{variant}", uf_oab, data_inicio,
                                 data_fim, sigla_tribunal, itens_por_pagina, timeout)
        total += count
        for item in items:
            seen.setdefault(item.get("id"), item)
    return {"count": total, "unique": len(seen), "items": list(seen.values())}


def fetch_certidao(comunicacao_hash, timeout=REQUEST_TIMEOUT):
    """Certidão oficial em PDF (pública). Retorna bytes; valida magic %PDF."""
    url = f"{BASE_URL}/{comunicacao_hash}/certidao"
    req = urllib.request.Request(url, headers={"Accept": "application/pdf"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
    except urllib.error.HTTPError as exc:
        raise DjenError(f"certidão {comunicacao_hash}: HTTP {exc.code}") from exc
    if not body.startswith(b"%PDF"):
        raise DjenError(f"certidão {comunicacao_hash}: resposta não é PDF")
    return body


def main():
    parser = argparse.ArgumentParser(description="Poll DJEN/Comunica intimações (público, sem auth)")
    parser.add_argument("--numero-oab", required=False, default=None, help="ex 14663 (variante exata; use --all-variants p/ 7)")
    parser.add_argument("--uf-oab", required=False, default=None, help="ex PB")
    parser.add_argument("--inicio", required=False, default=None, help="dataDisponibilizacaoInicio YYYY-MM-DD")
    parser.add_argument("--fim", required=False, default=None, help="dataDisponibilizacaoFim YYYY-MM-DD")
    parser.add_argument("--tribunal", default=None, help="ex TRF5")
    parser.add_argument("--pagina", type=int, default=1)
    parser.add_argument("--itens", type=int, default=DEFAULT_PAGE_SIZE)
    parser.add_argument("--all", action="store_true", help="paginar tudo")
    parser.add_argument("--all-variants", action="store_true", help="7 variantes de OAB")
    parser.add_argument("--certidao", default=None, help="hash da comunicação p/ baixar certidão PDF")
    parser.add_argument("--certidao-out", default=None, help="arquivo destino do PDF")
    args = parser.parse_args()
    with redirect_stdout(sys.stderr):
        if args.certidao:
            pdf = fetch_certidao(args.certidao)
            dest = args.certidao_out or f"cert-{args.certidao}.pdf"
            with open(dest, "wb") as fh:
                fh.write(pdf)
            print(json.dumps({"ok": True, "bytes": len(pdf), "file": dest}, ensure_ascii=False))
            return
        for required in ("numero_oab", "uf_oab", "inicio", "fim"):
            if not getattr(args, required):
                parser.error(f"--{required.replace('_', '-')} é obrigatório sem --certidao")
        if args.all_variants:
            base = "".join(ch for ch in args.numero_oab if ch.isdigit() or ch == "-")
            import re
            base = re.sub(r"-[A-Z]$", "", args.numero_oab)
            result = fetch_all_variants(base, args.uf_oab, args.inicio, args.fim,
                                        args.tribunal, args.itens)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return
        if args.all:
            count, items = fetch_all(args.numero_oab, args.uf_oab, args.inicio,
                                     args.fim, args.tribunal, args.itens)
            print(json.dumps({"count": count, "items": items}, ensure_ascii=False, indent=2))
            return
        result = fetch_page(args.numero_oab, args.uf_oab, args.inicio, args.fim,
                            args.tribunal, args.pagina, args.itens)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
