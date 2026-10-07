"""DJEN / Comunica PJe API client (public, no auth).

Fonte primária de intimações: ``https://comunica.pje.jus.br/`` via
``https://comunicaapi.pje.jus.br/api/v1/comunicacao``.
Sem autenticação. OAB/UF/tribunal/janela sempre via parâmetro —
nunca hardcoded (pertencem ao perfil do cliente).

Regras confirmadas em produção; o registro de pesquisa do cliente está no
projeto que executa o workflow:
- ``numeroOab`` é match exato de string: consultar as 7 variantes
  ("", -A, -B, -E, -N, -O, -S).
- ``itensPorPagina=50`` default; teto observado ~500, 1000 quebra com
  ``500 "sistema muito ocupado"``. Janelas curtas (1-7 dias).
- 500/429 e falha de transporte: retry limitado; caller decide dead-letter.
"""

import argparse
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date

BASE_URL = "https://comunicaapi.pje.jus.br/api/v1/comunicacao"
TRIBUNAL_URL = "https://comunicaapi.pje.jus.br/api/v1/comunicacao/tribunal"
CADERNO_URL = "https://comunicaapi.pje.jus.br/api/v1/caderno"
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 500
MIN_PAGE_SIZE = 5
MAX_CADERNO_BYTES = 512 * 1024 * 1024
OAB_VARIANTS = ("", "-A", "-B", "-E", "-N", "-O", "-S")
REQUEST_TIMEOUT = 30
MAX_RETRIES = 2
MAX_PDF_BYTES = 10 * 1024 * 1024


class DjenError(RuntimeError):
    """A public DJEN request failed; callers decide how to queue a retry."""


def build_url(
    numero_oab,
    uf_oab,
    data_inicio,
    data_fim,
    sigla_tribunal=None,
    pagina=1,
    itens_por_pagina=DEFAULT_PAGE_SIZE,
    *,
    texto=None,
    nome_advogado=None,
    nome_parte=None,
    numero_processo=None,
    numero_comunicacao=None,
    orgao_id=None,
    meio=None,
):
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
    extra = {
        "texto": texto,
        "nomeAdvogado": nome_advogado,
        "nomeParte": nome_parte,
        "numeroProcesso": numero_processo,
        "numeroComunicacao": numero_comunicacao,
        "orgaoId": orgao_id,
        "meio": meio,
    }
    for key, value in extra.items():
        if value is not None and str(value) != "":
            params[key] = value
    return BASE_URL + "?" + urllib.parse.urlencode(params)


def _read_json(url, timeout=REQUEST_TIMEOUT):
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.load(resp), resp.status
    except urllib.error.HTTPError as exc:
        exc.close()
        return None, exc.code


def _check_window(data_inicio, data_fim):
    try:
        start, end = date.fromisoformat(data_inicio), date.fromisoformat(data_fim)
    except (TypeError, ValueError) as exc:
        raise ValueError("datas devem ser YYYY-MM-DD") from exc
    if start > end:
        raise ValueError("data_inicio não pode ser posterior a data_fim")


def search_page(params, pagina=1, itens_por_pagina=DEFAULT_PAGE_SIZE, timeout=REQUEST_TIMEOUT):
    """Busca genérica por pessoa/caso/texto. Exige ≥1 chave de pesquisa.

    Chaves aceitas: siglaTribunal, texto, nomeParte, nomeAdvogado,
    numeroOab (+ufOab), numeroProcesso. Retorna dict com count/items.
    """
    query = dict(params or {})
    search_keys = ("siglaTribunal", "texto", "nomeParte", "nomeAdvogado", "numeroOab", "numeroProcesso")
    if not any(query.get(key) not in (None, "") for key in search_keys):
        raise ValueError("pesquisa exige siglaTribunal, texto, nomeParte, nomeAdvogado, numeroOab ou numeroProcesso")
    if int(pagina) < 1:
        raise ValueError("pagina deve ser positiva")
    if int(itens_por_pagina) < MIN_PAGE_SIZE or int(itens_por_pagina) > MAX_PAGE_SIZE:
        raise ValueError(f"itens_por_pagina deve estar entre {MIN_PAGE_SIZE} e {MAX_PAGE_SIZE}")
    query["pagina"] = int(pagina)
    query["itensPorPagina"] = int(itens_por_pagina)
    url = BASE_URL + "?" + urllib.parse.urlencode(query)
    last = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            data, status = _read_json(url, timeout)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last = type(exc).__name__
        else:
            if status == 200 and isinstance(data, dict) and data.get("status") != "error":
                items = data.get("items")
                try:
                    count = int(data.get("count"))
                except (TypeError, ValueError) as exc:
                    raise DjenError("DJEN response has no valid count") from exc
                if (
                    not isinstance(items, list)
                    or count < 0
                    or any(not isinstance(item, dict) or item.get("id") is None for item in items)
                ):
                    raise DjenError("DJEN response has invalid items")
                data["count"] = count
                return data
            last = f"HTTP {status}"
            if status != 429 and not 500 <= status <= 599:
                break
        if attempt < MAX_RETRIES:
            time.sleep(2**attempt)
    raise DjenError(f"DJEN request failed after {attempt + 1} attempt(s): {last}")


def fetch_page(
    numero_oab,
    uf_oab,
    data_inicio,
    data_fim,
    sigla_tribunal=None,
    pagina=1,
    itens_por_pagina=DEFAULT_PAGE_SIZE,
    timeout=REQUEST_TIMEOUT,
    **extra,
):
    """Uma página por OAB. Retorna dict com count/items. Levanta DjenError após retries."""
    if not str(numero_oab).strip() or not str(uf_oab).strip():
        raise ValueError("numero_oab e uf_oab são obrigatórios")
    _check_window(data_inicio, data_fim)
    params = {
        "numeroOab": str(numero_oab),
        "ufOab": uf_oab,
        "dataDisponibilizacaoInicio": data_inicio,
        "dataDisponibilizacaoFim": data_fim,
    }
    if sigla_tribunal:
        params["siglaTribunal"] = sigla_tribunal
    rename = {
        "texto": "texto",
        "nome_advogado": "nomeAdvogado",
        "nome_parte": "nomeParte",
        "numero_processo": "numeroProcesso",
        "numero_comunicacao": "numeroComunicacao",
        "orgao_id": "orgaoId",
        "meio": "meio",
    }
    for local, remote in rename.items():
        if extra.get(local) not in (None, ""):
            params[remote] = extra[local]
    return search_page(params, pagina, itens_por_pagina, timeout)


def search_all(params, itens_por_pagina=DEFAULT_PAGE_SIZE, timeout=REQUEST_TIMEOUT):
    """Todas as páginas de uma busca genérica. Retorna (count, items)."""
    first = search_page(params, 1, itens_por_pagina, timeout)
    count = int(first.get("count", 0))
    items = list(first["items"])
    seen = {item["id"] for item in items}
    if len(seen) != len(items):
        raise DjenError("DJEN first page contains duplicate IDs")
    pagina = 2
    while len(items) < count:
        page = search_page(params, pagina, itens_por_pagina, timeout)
        batch = page["items"]
        if not batch or page["count"] != count or any(item["id"] in seen for item in batch):
            raise DjenError(f"DJEN pagination incomplete at page {pagina}")
        items.extend(batch)
        seen.update(item["id"] for item in batch)
        pagina += 1
    if len(items) != count:
        raise DjenError("DJEN pagination count changed during fetch")
    return count, items


def fetch_all(
    numero_oab,
    uf_oab,
    data_inicio,
    data_fim,
    sigla_tribunal=None,
    itens_por_pagina=DEFAULT_PAGE_SIZE,
    timeout=REQUEST_TIMEOUT,
    **extra,
):
    """Todas as páginas da janela. Retorna (count, items)."""
    first = fetch_page(numero_oab, uf_oab, data_inicio, data_fim, sigla_tribunal, 1, itens_por_pagina, timeout, **extra)
    count = int(first.get("count", 0))
    items = list(first["items"])
    seen = {item["id"] for item in items}
    if len(seen) != len(items):
        raise DjenError("DJEN first page contains duplicate IDs")
    pagina = 2
    while len(items) < count:
        page = fetch_page(
            numero_oab, uf_oab, data_inicio, data_fim, sigla_tribunal, pagina, itens_por_pagina, timeout, **extra
        )
        batch = page["items"]
        if not batch or page["count"] != count or any(item["id"] in seen for item in batch):
            raise DjenError(f"DJEN pagination incomplete at page {pagina}")
        items.extend(batch)
        seen.update(item["id"] for item in batch)
        pagina += 1
    if len(items) != count:
        raise DjenError("DJEN pagination count changed during fetch")
    return count, items


def fetch_all_variants(
    numero_base,
    uf_oab,
    data_inicio,
    data_fim,
    sigla_tribunal=None,
    itens_por_pagina=DEFAULT_PAGE_SIZE,
    timeout=REQUEST_TIMEOUT,
    **extra,
):
    """Busca as 7 variantes de OAB e agrega sem duplicar (id = id da comunicação)."""
    seen = {}
    total = 0
    for variant in OAB_VARIANTS:
        count, items = fetch_all(
            f"{numero_base}{variant}", uf_oab, data_inicio, data_fim, sigla_tribunal, itens_por_pagina, timeout, **extra
        )
        total += count
        for item in items:
            seen.setdefault(item["id"], item)
    return {"count": total, "unique": len(seen), "items": list(seen.values())}


def fetch_certidao(comunicacao_hash, timeout=REQUEST_TIMEOUT):
    """Certidão oficial em PDF (pública). Retorna bytes; valida magic %PDF."""
    if not comunicacao_hash or not comunicacao_hash.isalnum():
        raise ValueError("hash da comunicação inválido")
    url = f"{BASE_URL}/{comunicacao_hash}/certidao"
    req = urllib.request.Request(url, headers={"Accept": "application/pdf"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(MAX_PDF_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise DjenError(f"certidão {comunicacao_hash}: HTTP {exc.code}") from exc
    if len(body) > MAX_PDF_BYTES:
        raise DjenError("certidão excede o limite de 10 MB")
    if not body.startswith(b"%PDF-"):
        raise DjenError(f"certidão {comunicacao_hash}: resposta não é PDF")
    return body


def list_tribunals(timeout=REQUEST_TIMEOUT):
    """113 siglas agrupadas por UF + dataUltimoEnvio (freshness) de cada tribunal."""
    data, status = _read_json(TRIBUNAL_URL, timeout)
    if status != 200 or not isinstance(data, list):
        raise DjenError(f"tribunal list failed: HTTP {status}")
    return data


def fetch_caderno(sigla_tribunal, data, meio="D", timeout=REQUEST_TIMEOUT):
    """Metadados do caderno diário + URL temporária (5 min) para download."""
    if not sigla_tribunal or not data:
        raise ValueError("sigla_tribunal e data (YYYY-MM-DD) são obrigatórios")
    if meio not in ("D", "E"):
        raise ValueError("meio deve ser D ou E")
    url = f"{CADERNO_URL}/{sigla_tribunal}/{data}/{meio}"
    payload, status = _read_json(url, timeout)
    if status != 200 or not isinstance(payload, dict) or "url" not in payload:
        raise DjenError(f"caderno {sigla_tribunal}/{data}/{meio} indisponível: HTTP {status}")
    return payload


def download_caderno(url, dest, timeout=REQUEST_TIMEOUT, max_bytes=MAX_CADERNO_BYTES):
    """Baixa o zip do caderno com teto de tamanho. Retorna bytes escritos."""
    req = urllib.request.Request(url, headers={"Accept": "application/zip"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            total = 0
            with open(dest, "wb") as fh:
                while True:
                    chunk = resp.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        raise DjenError("caderno excede o limite de bytes")
                    fh.write(chunk)
    except urllib.error.HTTPError as exc:
        raise DjenError(f"caderno download: HTTP {exc.code}") from exc
    return total


def main():
    parser = argparse.ArgumentParser(description="Poll DJEN/Comunica intimações (público, sem auth)")
    parser.add_argument(
        "--numero-oab", required=False, default=None, help="ex 14663 (variante exata; use --all-variants p/ 7)"
    )
    parser.add_argument("--uf-oab", required=False, default=None, help="ex PB")
    parser.add_argument("--inicio", required=False, default=None, help="dataDisponibilizacaoInicio YYYY-MM-DD")
    parser.add_argument("--fim", required=False, default=None, help="dataDisponibilizacaoFim YYYY-MM-DD")
    parser.add_argument("--tribunal", default=None, help="ex TRF5")
    parser.add_argument("--texto", default=None, help="busca textual no teor")
    parser.add_argument("--nome-advogado", default=None, help="ex ELISANDRA BECKER")
    parser.add_argument("--nome-parte", default=None, help="ex OI S.A.")
    parser.add_argument("--numero-processo", default=None, help="só dígitos")
    parser.add_argument("--numero-comunicacao", default=None)
    parser.add_argument("--orgao-id", default=None)
    parser.add_argument("--meio", default=None, choices=("D", "E"))
    parser.add_argument("--tribunals", action="store_true", help="listar siglas + freshness")
    parser.add_argument("--resolve", action="store_true", help="anexar portal resolvido a cada item")
    parser.add_argument("--link-doc", default=None, help="baixar + extrair texto do doc público (URL *.jus.br)")
    parser.add_argument("--caderno", default=None, help="SIGLA/YYYY-MM-DD/MEIO p/ metadados do caderno")
    parser.add_argument("--caderno-out", default=None, help="baixar o zip do caderno p/ arquivo")
    parser.add_argument("--pagina", type=int, default=1)
    parser.add_argument("--itens", type=int, default=DEFAULT_PAGE_SIZE)
    parser.add_argument("--all", action="store_true", help="paginar tudo")
    parser.add_argument("--all-variants", action="store_true", help="7 variantes de OAB")
    parser.add_argument("--normalized", action="store_true", help="normalizar os itens para roteamento")
    parser.add_argument(
        "--poll", action="store_true", help="buscar mudanças e atualizar estado local após leitura completa"
    )
    parser.add_argument("--state-dir", default=None, help="diretório do estado do poll")
    parser.add_argument("--certidao", default=None, help="hash da comunicação p/ baixar certidão PDF")
    parser.add_argument("--certidao-out", default=None, help="arquivo destino do PDF")
    parser.add_argument("--certidao-text", action="store_true", help="retornar texto extraído em JSON sem salvar PDF")
    parser.add_argument("--text-limit", type=int, default=50000)
    args = parser.parse_args()
    if args.link_doc:
        from jurisync.connectors.djen.documents import extract_text, fetch_link_document

        content_type, blob = fetch_link_document(args.link_doc)
        status, text = extract_text(blob, content_type)
        print(
            json.dumps(
                {
                    "url": args.link_doc,
                    "contentType": content_type,
                    "bytes": len(blob),
                    "extractionStatus": status,
                    "text": text[: args.text_limit],
                    "textTruncated": len(text) > args.text_limit,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    if args.tribunals:
        print(json.dumps(list_tribunals(), ensure_ascii=False, indent=2))
        return
    if args.caderno:
        try:
            sigla, data, meio = args.caderno.split("/")
        except ValueError:
            parser.error("--caderno usa o formato SIGLA/YYYY-MM-DD/MEIO")
        meta = fetch_caderno(sigla, data, meio)
        if args.caderno_out:
            meta["bytes_baixados"] = download_caderno(meta["url"], args.caderno_out)
            meta["arquivo"] = args.caderno_out
            del meta["url"]
        print(json.dumps(meta, ensure_ascii=False, indent=2))
        return
    if args.certidao:
        pdf = fetch_certidao(args.certidao)
        if args.certidao_text:
            from jurisync.core.document_text import extract_document_text

            if not 1 <= args.text_limit <= 100000:
                parser.error("--text-limit deve estar entre 1 e 100000")
            status, text = extract_document_text(pdf, "application/pdf")
            result = {
                "hash": args.certidao,
                "bytes": len(pdf),
                "extractionStatus": status,
                "text": text[: args.text_limit],
                "textTruncated": len(text) > args.text_limit,
            }
        else:
            dest = args.certidao_out or f"cert-{args.certidao}.pdf"
            with open(dest, "wb") as fh:
                fh.write(pdf)
            result = {"ok": True, "bytes": len(pdf), "file": dest}
    else:
        person_case = (
            args.texto
            or args.nome_advogado
            or args.nome_parte
            or args.numero_processo
            or args.numero_comunicacao
            or args.orgao_id
            or args.tribunal
        )
        required_fields = ["inicio", "fim"]
        if not (args.numero_oab and args.uf_oab) and not person_case:
            parser.error("--numero-oab + --uf-oab ou um critério de pessoa/caso/tribunal é obrigatório")
        if args.numero_oab and not args.uf_oab:
            parser.error("--uf-oab é obrigatório com --numero-oab")
        for required in required_fields:
            if not getattr(args, required):
                parser.error(f"--{required.replace('_', '-')} é obrigatório")
        extra = {
            "texto": args.texto,
            "nome_advogado": args.nome_advogado,
            "nome_parte": args.nome_parte,
            "numero_processo": args.numero_processo,
            "numero_comunicacao": args.numero_comunicacao,
            "orgao_id": args.orgao_id,
            "meio": args.meio,
        }
        base = re.sub(r"-(?:A|B|E|N|O|S)$", "", args.numero_oab or "", flags=re.I)
        if args.poll:
            if not args.all_variants:
                parser.error("--poll exige --all-variants para manter estado consistente")
            if any(extra.values()):
                parser.error("--poll não aceita filtros adicionais: o estado cobre toda a OAB/tribunal")
            if not args.numero_oab or not args.uf_oab:
                parser.error("--poll exige --numero-oab + --uf-oab")
            from jurisync.connectors.djen.poll import poll_window

            result = poll_window(base, args.uf_oab, args.tribunal, args.inicio, args.fim, args.state_dir, args.itens)
        elif args.all_variants:
            if not args.numero_oab or not args.uf_oab:
                parser.error("--all-variants exige --numero-oab + --uf-oab")
            result = fetch_all_variants(base, args.uf_oab, args.inicio, args.fim, args.tribunal, args.itens, **extra)
        elif args.all:
            if not args.numero_oab:
                parser.error("--all exige --numero-oab + --uf-oab")
            count, items = fetch_all(
                args.numero_oab, args.uf_oab, args.inicio, args.fim, args.tribunal, args.itens, **extra
            )
            result = {"count": count, "items": items}
        else:
            params = {"dataDisponibilizacaoInicio": args.inicio, "dataDisponibilizacaoFim": args.fim}
            if args.numero_oab:
                params.update(numeroOab=args.numero_oab, ufOab=args.uf_oab)
            if args.tribunal:
                params["siglaTribunal"] = args.tribunal
            rename = {
                "texto": "texto",
                "nome_advogado": "nomeAdvogado",
                "nome_parte": "nomeParte",
                "numero_processo": "numeroProcesso",
                "numero_comunicacao": "numeroComunicacao",
                "orgao_id": "orgaoId",
                "meio": "meio",
            }
            for local, remote in rename.items():
                if extra.get(local) not in (None, ""):
                    params[remote] = extra[local]
            result = search_page(params, args.pagina, args.itens)
        if args.normalized and not args.poll:
            from jurisync.connectors.djen.normalize import normalize_item

            result["items"] = [normalize_item(item) for item in result["items"]]
        if args.resolve and isinstance(result.get("items"), list):
            from jurisync.connectors.djen.documents import resolve_portal

            for item in result["items"]:
                item["portal"] = resolve_portal(item)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
