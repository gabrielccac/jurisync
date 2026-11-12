"""Values the system calculates, so that totals are never typed by hand."""

import datetime as dt
from decimal import Decimal, InvalidOperation

UNITS = [
    "zero",
    "um",
    "dois",
    "três",
    "quatro",
    "cinco",
    "seis",
    "sete",
    "oito",
    "nove",
    "dez",
    "onze",
    "doze",
    "treze",
    "quatorze",
    "quinze",
    "dezesseis",
    "dezessete",
    "dezoito",
    "dezenove",
]
TENS = ["_", "_", "vinte", "trinta", "quarenta", "cinquenta", "sessenta", "setenta", "oitenta", "noventa"]
HUNDREDS = [
    "_",
    "cento",
    "duzentos",
    "trezentos",
    "quatrocentos",
    "quinhentos",
    "seiscentos",
    "setecentos",
    "oitocentos",
    "novecentos",
]
MONTHS = [
    "janeiro",
    "fevereiro",
    "março",
    "abril",
    "maio",
    "junho",
    "julho",
    "agosto",
    "setembro",
    "outubro",
    "novembro",
    "dezembro",
]


def money(value):
    """Read ``20000``, ``"20000.50"`` or ``"20.000,50"`` as a Decimal; ``None`` when empty."""
    if value in (None, ""):
        return None
    text = str(value).replace("R$", "").strip()
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    try:
        return Decimal(text)
    except InvalidOperation as error:
        raise ValueError(f"Invalid amount: {value}") from error


def format_money(value):
    whole, cents = f"{value:.2f}".split(".")
    groups = f"{int(whole):,}".replace(",", ".")
    return f"{groups},{cents}"


def _below_thousand(number):
    if number == 100:
        return "cem"
    parts = []
    if number >= 100:
        parts.append(HUNDREDS[number // 100])
        number %= 100
    if number >= 20:
        parts.append(TENS[number // 10])
        number %= 10
    if number or not parts:
        parts.append(UNITS[number])
    return " e ".join(parts)


def _integer_words(number):
    if number < 1000:
        return _below_thousand(number)
    millions, rest = divmod(number, 1_000_000)
    thousands, units = divmod(rest, 1000)
    parts = []
    if millions:
        parts.append(f"{_below_thousand(millions)} {'milhão' if millions == 1 else 'milhões'}")
    if thousands:
        parts.append("mil" if thousands == 1 else f"{_below_thousand(thousands)} mil")
    if units:
        parts.append(_below_thousand(units))
    if len(parts) > 1 and units and (units <= 100 or units % 100 == 0):
        return " e ".join([" ".join(parts[:-1]), parts[-1]])
    return " ".join(parts)


def money_in_words(value):
    whole, cents = divmod(int((value * 100).to_integral_value()), 100)
    parts = []
    if whole:
        joiner = " de reais" if whole % 1_000_000 == 0 else " reais"
        parts.append(_integer_words(whole) + (" real" if whole == 1 else joiner))
    if cents:
        parts.append(_integer_words(cents) + (" centavo" if cents == 1 else " centavos"))
    return " e ".join(parts) or "zero reais"


def date_in_words(value):
    day = value if isinstance(value, dt.date) else _parse_date(value)
    return f"{day.day:02d} de {MONTHS[day.month - 1]} de {day.year}"


def _parse_date(value):
    text = str(value).strip()
    for pattern in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return dt.datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    raise ValueError(f"Invalid date: {value}")


def apply(case):
    """Fill the calculated fields that can be derived from the dossier; other ``calc.*`` stay required."""
    moral = money(case.get("processo.valor_moral"))
    material = money(case.get("calc.valor_material_total"))
    if material is None:
        items = (case.author.get("autor") or {}).get("despesas") or []
        material = sum((money(item.get("valor")) for item in items), Decimal(0)) if items else None
        if material is not None:
            case.calculated["valor_material_total"] = format_money(material)
    compensation = money(case.get("calc.des_valor_aprox")) if case.get("peca.usar_tese_pretericao") else None
    if moral is not None:
        total = moral + (material or 0) + (compensation or 0)
        case.calculated["valor_causa"] = format_money(total)
        case.calculated["valor_causa_extenso"] = money_in_words(total)
    if case.get("viagem.data_voo_original"):
        case.calculated["voo_em_ou_apos_26_10_2026"] = _parse_date(case.get("viagem.data_voo_original")) >= dt.date(
            2026, 10, 26
        )
    if case.get("processo.data_peca"):
        case.calculated["data_peca_extenso"] = date_in_words(case.get("processo.data_peca"))
