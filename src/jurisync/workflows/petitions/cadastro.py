"""Public registry lookups for a petition: a company's registration (CNPJ) and the municipality of a postal code (CEP).

CNPJ: Receita Federal data through BrasilAPI, with publica.cnpj.ws as fallback (both public, rate limited).
CEP: ViaCEP. Read only; the result names its source, to be checked against the company's own documents.
"""

import json
import os
import re
import time
from pathlib import Path

from curl_cffi import requests

TIMEOUT = 40


class RegistryError(RuntimeError):
    pass


def _get_json(url, attempts=3):
    """GET a JSON document; a rate limit (HTTP 429) is waited out a few times before giving up."""
    for attempt in range(attempts):
        try:
            response = requests.get(url, timeout=TIMEOUT)
        except requests.exceptions.RequestException as error:
            raise RegistryError(f"Consulta falhou: {type(error).__name__}") from error
        if response.status_code != 429:
            break
        time.sleep(5 * (attempt + 1))
    if response.status_code != 200:
        return None, response.status_code
    try:
        return response.json(), 200
    except ValueError:
        return None, response.status_code


def _digits(value):
    return re.sub(r"\D", "", str(value or ""))


def _format_cnpj(digits):
    return f"{digits[:2]}.{digits[2:5]}.{digits[5:8]}/{digits[8:12]}-{digits[12:]}"


def _format_cep(digits):
    return f"{digits[:5]}-{digits[5:]}"


def _cache_file(digits):
    folder = Path(os.environ.get("JURISYNC_CACHE_DIR") or Path.home() / ".cache" / "jurisync") / "cnpj"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{digits}.json"


def company(cnpj):
    """Name, status and address of a company, ready for the qualification of a defendant (cached for a day)."""
    digits = _digits(cnpj)
    if len(digits) != 14:
        raise RegistryError("CNPJ deve ter 14 dígitos")
    cached = _cache_file(digits)
    if cached.exists() and time.time() - cached.stat().st_mtime < 86400:
        return json.loads(cached.read_text(encoding="utf-8"))
    result = _company(digits)
    cached.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return result


def _company(digits):
    data, status = _get_json(f"https://brasilapi.com.br/api/cnpj/v1/{digits}")
    if data:
        address = [
            f"{data.get('descricao_tipo_de_logradouro') or ''} {data.get('logradouro') or ''}".strip(),
            data.get("numero"),
            data.get("complemento"),
            data.get("bairro"),
            f"{data.get('municipio')}/{data.get('uf')}",
        ]
        result = {
            "razao_social": data.get("razao_social"),
            "nome_fantasia": data.get("nome_fantasia"),
            "situacao": data.get("descricao_situacao_cadastral"),
            "cep": data.get("cep"),
            "fonte": "Receita Federal (BrasilAPI)",
        }
    else:
        data, status2 = _get_json(f"https://publica.cnpj.ws/cnpj/{digits}")
        if not data:
            raise RegistryError(f"CNPJ não encontrado (HTTP {status}/{status2})")
        place = data.get("estabelecimento") or {}
        address = [
            f"{place.get('tipo_logradouro') or ''} {place.get('logradouro') or ''}".strip(),
            place.get("numero"),
            place.get("complemento"),
            place.get("bairro"),
            f"{(place.get('cidade') or {}).get('nome')}/{(place.get('estado') or {}).get('sigla')}",
        ]
        result = {
            "razao_social": data.get("razao_social"),
            "nome_fantasia": place.get("nome_fantasia"),
            "situacao": place.get("situacao_cadastral"),
            "cep": place.get("cep"),
            "fonte": "Receita Federal (publica.cnpj.ws)",
        }
    parts = [" ".join(str(part).split()) for part in address if part and str(part).strip()]
    cep = _digits(result["cep"])
    result["cnpj"] = _format_cnpj(digits)
    result["endereco"] = ", ".join(parts) + (f", CEP {_format_cep(cep)}" if len(cep) == 8 else "")
    return result


def postal_code(cep):
    """Municipality and state of a CEP (the court's district follows the author's municipality)."""
    digits = _digits(cep)
    if len(digits) != 8:
        raise RegistryError("CEP deve ter 8 dígitos")
    data, status = _get_json(f"https://viacep.com.br/ws/{digits}/json/")
    if not data or data.get("erro"):
        raise RegistryError(f"CEP não encontrado (HTTP {status})")
    return {
        "cep": _format_cep(digits),
        "logradouro": data.get("logradouro"),
        "bairro": data.get("bairro"),
        "municipio": data.get("localidade"),
        "uf": data.get("uf"),
        "ibge": data.get("ibge"),
        "fonte": "ViaCEP",
    }
