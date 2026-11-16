"""Official exchange rates for a petition: the Central Bank's PTAX and the IMF's value of the SDR (DES).

- A currency (USD, EUR, GBP...): PTAX selling rate of the Banco Central do Brasil for the day; on a day without a
  bulletin (weekend, holiday) the last previous one.
- The SDR (DES): BCB has no SDR rate, so reais per SDR = PTAX dollar of the day × dollars per SDR from the IMF
  exchange-rate data (end of the latest published month up to that day). The IMF's own reais-per-SDR for that
  month-end is returned too, for reference.

Read only, public data. The result names its sources, to be recorded in the case file (``processo.des_fonte``).
"""

import datetime as dt
import re
from decimal import Decimal

from curl_cffi import requests

PTAX = "https://olinda.bcb.gov.br/olinda/servico/PTAX/versao/v1/odata"
IMF = "https://api.imf.org/external/sdmx/2.1/data/IMF.STA,ER/{country}.XDC_XDR.EOP_RT.M"
TIMEOUT = 60
LOOKBACK_DAYS = 10


class QuoteError(RuntimeError):
    pass


def _get(url, **params):
    try:
        response = requests.get(url, params=params, timeout=TIMEOUT)
    except requests.exceptions.RequestException as error:
        raise QuoteError(f"Consulta falhou: {type(error).__name__}") from error
    if response.status_code != 200:
        raise QuoteError(f"Consulta respondeu HTTP {response.status_code}")
    return response


def _date(value):
    return value if isinstance(value, dt.date) else dt.date.fromisoformat(str(value))


PTAX_CURRENCIES = {"USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "DKK", "NOK", "SEK"}


def ptax(currency, day):
    """``(selling rate, date of the bulletin)`` for one currency, going back to the last bulletin."""
    currency = currency.upper()
    if currency not in PTAX_CURRENCIES:
        raise QuoteError(f"{currency} não tem PTAX")
    day = _date(day)
    for back in range(LOOKBACK_DAYS):
        when = day - dt.timedelta(days=back)
        stamp = when.strftime("%m-%d-%Y")
        if currency == "USD":
            url = f"{PTAX}/CotacaoDolarDia(dataCotacao=@dataCotacao)"
            data = _get(url, **{"@dataCotacao": f"'{stamp}'", "$format": "json"}).json()
        else:
            url = f"{PTAX}/CotacaoMoedaDia(moeda=@moeda,dataCotacao=@dataCotacao)"
            data = _get(url, **{"@moeda": f"'{currency}'", "@dataCotacao": f"'{stamp}'", "$format": "json"}).json()
        values = data.get("value") or []
        closing = [item for item in values if item.get("tipoBoletim") in (None, "Fechamento", "Fechamento PTAX")]
        if closing or values:
            item = (closing or values)[-1]
            return Decimal(str(item["cotacaoVenda"])), when
    raise QuoteError(f"Sem PTAX de {currency} nos {LOOKBACK_DAYS} dias até {day.isoformat()}")


def _imf_month_end(country, day):
    """``(rate, 'YYYY-MM')`` of the latest IMF month-end rate (national currency per SDR) up to ``day``."""
    start = (day.replace(day=1) - dt.timedelta(days=200)).strftime("%Y-%m")
    text = _get(IMF.format(country=country), startPeriod=start, endPeriod=day.strftime("%Y-%m")).text
    points = re.findall(r'TIME_PERIOD="(\d{4})-M(\d{2})" OBS_VALUE="([\d.]+)"', text)
    if not points:
        raise QuoteError(f"O FMI não devolveu a taxa do DES para {country} até {day.isoformat()}")
    # A month-end after the day is not known on that day: only months closed by then count.
    last_day = (day.replace(day=28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1)
    closed = [point for point in points if (int(point[0]), int(point[1])) < (day.year, day.month) or day == last_day]
    if not closed:
        raise QuoteError(f"O FMI ainda não tem mês fechado até {day.isoformat()} para {country}")
    year, month, value = max(closed)
    return Decimal(value), f"{year}-{month}"


def _br(number):
    return f"{number}".replace(".", ",")


def des(day):
    """Reais per SDR for a day, with its sources."""
    day = _date(day)
    dollar, bulletin = ptax("USD", day)
    usd_per_sdr, month = _imf_month_end("USA", day)
    brl_month_end, _ = _imf_month_end("BRA", day)
    rate = (dollar * usd_per_sdr).quantize(Decimal("0.0001"))
    return {
        "moeda": "XDR",
        "data": day.isoformat(),
        "cotacao": f"{rate}".replace(".", ","),
        "fonte": (
            f"PTAX venda do dólar (Banco Central do Brasil) de {bulletin.strftime('%d/%m/%Y')}, R$ {_br(dollar)}, × "
            f"US$ {_br(usd_per_sdr.quantize(Decimal('0.000001')))} por DES (FMI, taxa de fim de mês {month})"
        ),
        "referencia_fmi_reais_por_des_fim_de_mes": f"{brl_month_end.quantize(Decimal('0.0001'))}".replace(".", ","),
    }


CLOSING = "https://www4.bcb.gov.br/Download/fechamento/{date}.csv"


def closing(currency, day):
    """``(selling rate, date)`` from the Central Bank's daily closing file, which lists every currency."""
    currency = currency.upper()
    day = _date(day)
    for back in range(LOOKBACK_DAYS):
        when = day - dt.timedelta(days=back)
        try:
            response = requests.get(CLOSING.format(date=when.strftime("%Y%m%d")), timeout=TIMEOUT)
        except requests.exceptions.RequestException as error:
            raise QuoteError(f"Consulta falhou: {type(error).__name__}") from error
        if response.status_code != 200:
            continue
        for line in response.text.splitlines():
            fields = line.split(";")
            if len(fields) >= 6 and fields[3] == currency:
                return Decimal(fields[5].replace(",", ".")), when
    raise QuoteError(f"Sem cotação de {currency} no Banco Central nos {LOOKBACK_DAYS} dias até {day.isoformat()}")


def currency(code, day):
    try:
        rate, bulletin = ptax(code, day)
    except QuoteError:
        rate, bulletin = closing(code, day)
        return {
            "moeda": code.upper(),
            "data": _date(day).isoformat(),
            "cotacao": f"{rate}".replace(".", ","),
            "fonte": f"Banco Central do Brasil, taxa de fechamento (venda) de {bulletin.strftime('%d/%m/%Y')}",
        }
    return {
        "moeda": code.upper(),
        "data": _date(day).isoformat(),
        "cotacao": f"{rate}".replace(".", ","),
        "fonte": f"PTAX venda (Banco Central do Brasil) de {bulletin.strftime('%d/%m/%Y')}",
    }


def quote(code, day):
    return des(day) if code.upper() in ("XDR", "DES", "SDR") else currency(code, day)
