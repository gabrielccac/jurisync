"""Official record of a flight from ANAC's open data (VRA, Voo Regular Ativo): scheduled and actual times and status.

ANAC publishes one CSV per month (``siros.anac.gov.br/siros/registros/diversos/vra/<ano>/VRA_<ano>_<mês>.csv``),
usually some weeks after the month ends. The file is cached on this machine. A month not yet published raises
``FlightError``: then the record has to come from another source (the airline's app, a print, FlightAware).
"""

import csv
import datetime as dt
import io
import os
from pathlib import Path

from curl_cffi import requests

BASE = "https://siros.anac.gov.br/siros/registros/diversos/vra/{year}/VRA_{year}_{month:02d}.csv"
TIMEOUT = 180
# IATA code of the airline (as on tickets) to ANAC's ICAO code.
AIRLINES = {
    "AD": "AZU",
    "G3": "GLO",
    "LA": "TAM",
    "JJ": "TAM",
    "2Z": "PTB",
    "H2": "SKU",
    "JA": "JAT",
    "AA": "AAL",
    "CM": "CMP",
    "TP": "TAP",
    "AR": "ARG",
    "AV": "AVA",
    "KL": "KLM",
    "AF": "AFR",
    "LH": "DLH",
    "IB": "IBE",
    "UX": "AEA",
    "DL": "DAL",
    "UA": "UAL",
}


class FlightError(RuntimeError):
    pass


def _cache():
    folder = Path(os.environ.get("JURISYNC_CACHE_DIR") or Path.home() / ".cache" / "jurisync") / "vra"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def month_file(year, month):
    """The month's VRA file, downloaded once."""
    path = _cache() / f"VRA_{year}_{month:02d}.csv"
    if path.exists() and path.stat().st_size > 1000:
        return path
    url = BASE.format(year=year, month=month)
    try:
        response = requests.get(url, timeout=TIMEOUT)
    except requests.exceptions.RequestException as error:
        raise FlightError(f"Consulta à ANAC falhou: {type(error).__name__}") from error
    if response.status_code == 404:
        raise FlightError(f"A ANAC ainda não publicou o VRA de {month:02d}/{year}.")
    if response.status_code != 200 or not response.content.startswith(b"Sigla"):
        raise FlightError(f"A ANAC respondeu HTTP {response.status_code} para o VRA de {month:02d}/{year}.")
    path.write_bytes(response.content)
    return path


def airline_code(value):
    code = str(value).strip().upper()
    return AIRLINES.get(code, code)


def _day(text):
    return dt.datetime.strptime(text.strip(), "%d/%m/%Y %H:%M").date() if text and text.strip() else None


def find(airline, number, day, origin=None, destination=None):
    """Rows of the flight scheduled (or flown) on ``day``; the day before and after are kept for overnight flights."""
    day = day if isinstance(day, dt.date) else dt.date.fromisoformat(str(day))
    icao = airline_code(airline)
    number = str(number).strip().lstrip("0")
    rows = []
    months = {(day.year, day.month)}
    for delta in (-1, 1):
        near = day + dt.timedelta(days=delta)
        months.add((near.year, near.month))
    for year, month in sorted(months):
        try:
            path = month_file(year, month)
        except FlightError:
            if (year, month) == (day.year, day.month):
                raise
            continue
        with path.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle, delimiter=";"):
                if row["Sigla ICAO Empresa Aérea"] != icao or row["Número Voo"].lstrip("0") != number:
                    continue
                scheduled = _day(row["Partida Prevista"]) or _day(row["Partida Real"])
                if scheduled != day:
                    continue
                if origin and row["Sigla ICAO Aeroporto Origem"] != origin.upper():
                    continue
                if destination and row["Sigla ICAO Aeroporto Destino"] != destination.upper():
                    continue
                rows.append(row)
    return [_record(row) for row in rows]


def _record(row):
    return {
        "empresa": f"{row['Empresa Aérea']} ({row['Sigla ICAO Empresa Aérea']})",
        "voo": row["Número Voo"],
        "origem": f"{row['Sigla ICAO Aeroporto Origem']} – {row['Descrição Aeroporto Origem']}",
        "destino": f"{row['Sigla ICAO Aeroporto Destino']} – {row['Descrição Aeroporto Destino']}",
        "partida_prevista": row["Partida Prevista"],
        "partida_real": row["Partida Real"] or None,
        "chegada_prevista": row["Chegada Prevista"],
        "chegada_real": row["Chegada Real"] or None,
        "situacao": row["Situação Voo"],
        "justificativa": row.get("Justificativa") or None,
        "fonte": "ANAC, Voo Regular Ativo (VRA), dados abertos de "
        f"{row['Referência'][3:10] if row.get('Referência') else ''}; horários de Brasília (UTC-3)",
    }


def records_text(records):
    out = io.StringIO()
    for item in records:
        out.write(
            f"{item['empresa']} {item['voo']}: {item['origem']} → {item['destino']} | prevista "
            f"{item['partida_prevista']} → {item['chegada_prevista']} | real {item['partida_real'] or '—'} → "
            f"{item['chegada_real'] or '—'} | {item['situacao']}\n"
        )
    return out.getvalue()
