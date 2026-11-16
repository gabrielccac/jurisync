"""Values the system calculates, so that totals are never typed by hand."""

import datetime as dt
import re
from decimal import Decimal

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


GROUPED = r"[1-9]\d{0,2}(?:\.\d{3})+"
AMOUNT = re.compile(rf"(?P<whole>\d+|{GROUPED})(?:,(?P<cents>\d{{1,2}}))?")
PLAIN_DECIMAL = re.compile(r"\d+\.\d{1,2}")
SCALES = (None, None, ("milhão", "milhões"), ("bilhão", "bilhões"), ("trilhão", "trilhões"))


def digits(value):
    """Only the digits of a CPF, a case number or any other formatted identifier."""
    return re.sub(r"\D", "", str(value or ""))


def money(value):
    """Read an amount as a Decimal; ``None`` when empty.

    Text is Brazilian: ``.`` separates thousands and ``,`` the cents, so ``"20.000"`` is twenty thousand and
    ``"20.000,50"`` has fifty cents. A dot followed by one or two digits and no comma (``"20000.50"``) is the decimal
    point of a number written by a program. Anything else (``"20.0000"``, ``"1.23.4"``, ``"20,000.50"``) is refused
    instead of guessed.
    """
    if value is None or value == "":
        return None
    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        number = Decimal(str(value))
        if not number.is_finite() or number < 0:
            raise ValueError(f"Invalid amount: {value!r}")
        return number
    text = re.sub(r"^R\$", "", str(value).strip(), flags=re.I).strip() if isinstance(value, str) else ""
    match = AMOUNT.fullmatch(text)
    if match:
        return Decimal(f"{match['whole'].replace('.', '')}.{match['cents'] or '0'}".removesuffix(".0"))
    if PLAIN_DECIMAL.fullmatch(text):
        return Decimal(text)
    raise ValueError(f"Invalid amount: {value!r} (write it as 20000,00 or 20.000,00)")


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
    """Cardinal in words, below one quadrillion. "e" links the last group when it is under 100 or a round hundred."""
    if number >= 1000 ** len(SCALES):
        raise ValueError("Invalid amount: too large to write in words")
    groups = []
    position = 0
    while number:
        number, group = divmod(number, 1000)
        if group:
            groups.append((group, position))
        position += 1
    if not groups:
        return UNITS[0]
    words = []
    for group, position in reversed(groups):
        if position == 0:
            words.append(_below_thousand(group))
        elif position == 1:
            words.append("mil" if group == 1 else f"{_below_thousand(group)} mil")
        else:
            words.append(f"{_below_thousand(group)} {SCALES[position][group != 1]}")
    last = groups[0][0]
    if len(words) > 1 and (last < 100 or last % 100 == 0):
        return f"{' '.join(words[:-1])} e {words[-1]}"
    return " ".join(words)


def rate(value):
    """A quotation, which may have more than two decimals (``"7,6354"``); amounts go through ``money``."""
    if isinstance(value, str) and re.fullmatch(r"\d+,\d+", value.strip()):
        return Decimal(value.strip().replace(",", "."))
    return money(value)


def money_in_words(value):
    if value < 0:
        raise ValueError(f"Invalid amount: {value} is negative")
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


def _amount(case, name):
    try:
        return money(case.get(name))
    except ValueError as error:
        raise ValueError(f"{name}: {error}") from error


def _convert(items):
    """An expense in another currency: valor_original × cotacao (the exchange rate of its day, with its source)."""
    for position, item in enumerate(items, 1):
        if isinstance(item, dict) and item.get("valor") in (None, "") and item.get("valor_original") not in (None, ""):
            try:
                amount = money(item["valor_original"]) * rate(item.get("cotacao"))
            except (TypeError, ValueError) as error:
                raise ValueError(
                    f"autor.despesas[{position}]: valor_original e cotacao (câmbio do dia) são necessários"
                ) from error
            item["valor"] = format_money(amount.quantize(Decimal("0.01")))
            item["valor_convertido"] = True


def _expenses_total(items):
    total = Decimal(0)
    for position, item in enumerate(items, 1):
        label = f"autor.despesas[{position}]"
        if not isinstance(item, dict) or item.get("valor") in (None, ""):
            raise ValueError(f"{label} has no valor (the amount of the expense)")
        try:
            total += money(item["valor"])
        except ValueError as error:
            raise ValueError(f"{label}.valor: {error}") from error
    return total


# Durations of the trip: (calculated field, (date field, time field) from, (date field, time field) to).
# A missing date is the original flight's date; a time "to" earlier than "from" without its own date crossed midnight.
DURATIONS = (
    (
        "atraso_anunciado",
        ("viagem.data_voo_original", "viagem.horario_partida_original"),
        ("viagem.data_nova", "viagem.novo_horario_partida"),
    ),
    (
        "atraso_efetivo",
        ("viagem.data_voo_original", "viagem.horario_partida_original"),
        ("viagem.data_decolagem_efetiva", "viagem.horario_decolagem_efetivo"),
    ),
    (
        "atraso_chegada",
        ("viagem.data_chegada_original", "viagem.horario_chegada_original"),
        ("viagem.data_chegada_efetiva", "viagem.horario_chegada_efetivo"),
    ),
    (
        "antecedencia_comunicacao",
        ("viagem.data_comunicacao", "viagem.hora_comunicacao"),
        ("viagem.data_voo_original", "viagem.horario_partida_original"),
    ),
    (
        "tempo_painel_apos_partida",
        ("viagem.data_voo_original", "viagem.horario_partida_original"),
        ("viagem.data_painel", "viagem.horario_painel"),
    ),
)


def _moment(case, date_field, time_field):
    time = case.get(time_field)
    if time in (None, ""):
        return None, False
    own_date = case.get(date_field)
    date = own_date or case.get("viagem.data_voo_original")
    if date in (None, ""):
        return None, False
    match = re.fullmatch(r"(\d{1,2})[:hH](\d{2})?", str(time).strip())
    if not match:
        raise ValueError(f"{time_field}: hora inválida {time!r} (use 14:30)")
    moment = dt.datetime.combine(_parse_date(date), dt.time(int(match[1]), int(match[2] or 0)))
    return moment, own_date not in (None, "")


def duration_text(minutes):
    """'10 horas e 45 minutos', '1 hora', '45 minutos'."""
    hours, rest = divmod(int(minutes), 60)
    parts = []
    if hours:
        parts.append(f"{hours} hora" + ("s" if hours != 1 else ""))
    if rest or not hours:
        parts.append(f"{rest} minuto" + ("s" if rest != 1 else ""))
    return " e ".join(parts)


def _durations(case):
    for name, start, end in DURATIONS:
        if case.get(f"calc.{name}") not in (None, ""):
            continue
        begin, _ = _moment(case, *start)
        finish, dated = _moment(case, *end)
        if begin is None or finish is None:
            continue
        # Only a delay crosses midnight: a panel photo or a notice may come before the departure.
        if finish < begin and not dated and name.startswith("atraso_"):
            finish += dt.timedelta(days=1)
        minutes = int((finish - begin).total_seconds() // 60)
        if name == "tempo_painel_apos_partida" and minutes < 0:
            case.calculated["painel_antes_da_partida"] = True
            continue
        if name == "antecedencia_comunicacao":
            case.calculated["comunicacao_apos_partida"] = minutes <= 0
            if minutes <= 0:
                continue
        elif minutes < 0:
            raise ValueError(f"calc.{name}: o horário final é anterior ao inicial; confira datas e horas")
        case.calculated[name] = duration_text(minutes)
        case.calculated[f"{name}_minutos"] = minutes
        if name == "atraso_efetivo":
            case.calculated["atraso_efetivo_piso"] = duration_text(minutes // 60 * 60)
    effective = case.calculated.get("atraso_chegada_minutos", case.calculated.get("atraso_efetivo_minutos"))
    if effective is not None:
        case.calculated["atraso_4h_ou_mais"] = effective >= 240


def _highest_quote(case):
    quotes = []
    for name in ("processo.des_cotacao", "processo.des_cotacao_data_voo", "processo.des_cotacao_data_peca"):
        try:
            quotes.append(rate(case.get(name)))
        except ValueError as error:
            raise ValueError(f"{name}: {error}") from error
    quotes = [quote for quote in quotes if quote is not None]
    return max(quotes) if quotes else None


def _compensation(case):
    """DES amount in reais: the number of DES times the highest quotation informed (the office asks the highest)."""
    quotes = []
    for name in ("processo.des_cotacao", "processo.des_cotacao_data_voo", "processo.des_cotacao_data_peca"):
        try:
            quotes.append(rate(case.get(name)))
        except ValueError as error:
            raise ValueError(f"{name}: {error}") from error
    quotes = [quote for quote in quotes if quote is not None]
    units = case.get("processo.des_quantidade")
    if units in (None, ""):
        international = case.get("viagem.internacional")
        if international is None:
            return None
        units = 500 if international else 250
    case.calculated["des_quantidade"] = str(units)
    if not quotes:
        return None
    return (Decimal(str(units)) * max(quotes)).quantize(Decimal("0.01"))


# National minimum wage by year, with the act that set it. The small-claims court takes up to 40 of them.
MINIMUM_WAGE = {
    2025: (Decimal("1518"), "Decreto nº 12.342/2024"),
    2026: (Decimal("1621"), "Decreto nº 12.797/2025"),
}


def small_claims_ceiling(day=None):
    """40 minimum wages (Lei 9.099/1995, art. 3º, I) in the year of the petition; ``None`` for an unknown year."""
    year = _parse_date(day).year if day else dt.date.today().year
    wage = MINIMUM_WAGE.get(year)
    return wage[0] * 40 if wage else None


def apply(case):
    """Fill the calculated fields that can be derived from the dossier; other ``calc.*`` stay required.

    A dossier amount that cannot be read raises ``ValueError`` naming the field.
    """
    authors = len(case.group)
    moral = _amount(case, "processo.valor_moral")
    if moral is not None and authors > 1:
        case.calculated["valor_moral_total"] = format_money(moral * authors)
        moral *= authors
    material = _amount(case, "calc.valor_material_total")
    refundable = _amount(case, "calc.valor_restituivel")
    if material is None:
        dossier_authors = case.dossier.get("autores") or []
        items = []
        for index in case.group:
            own = (dossier_authors[index].get("autor") or {}).get("despesas") or []
            if not isinstance(own, list):
                raise ValueError("autor.despesas must be a list of expenses")
            _convert(own)
            converted = [
                {
                    "descricao": item.get("descricao"),
                    "original": f"{item.get('moeda', '')} {item.get('valor_original')}",
                    "cotacao": item.get("cotacao"),
                    "valor": item.get("valor"),
                }
                for item in own
                if isinstance(item, dict) and item.get("valor_convertido")
            ]
            if converted:
                case.calculated.setdefault("despesas_convertidas", []).extend(converted)
            items += own
            if own and case.joint:
                name = (dossier_authors[index].get("autor") or {}).get("nome") or f"autor {index + 1}"
                case.calculated.setdefault("material_por_autor", {})[name] = format_money(_expenses_total(own))
        material = _expenses_total(items) if items else None
        if material is not None:
            case.calculated["valor_material_total"] = format_money(material)
            # Art. 42 of the CDC: only what the defendant itself charged goes back in double; an expense paid to a
            # third party (hotel, food, transport) is plain material damage.
            charged = [item for item in items if str(item.get("pago_a") or "").casefold() == "re"]
            case.calculated["despesas_sem_pagador"] = any(not item.get("pago_a") for item in items)
            case.calculated["despesas_pagas_por_terceiro"] = any(item.get("pago_por") for item in items)
            if charged:
                # What the defendant already gave back (a partial refund) is not claimed again.
                returned = sum((money(item.get("reembolsado")) or Decimal(0) for item in charged), Decimal(0))
                paid = _expenses_total(charged)
                refundable = paid - returned
                material -= returned
                case.calculated["valor_material_total"] = format_money(material)
                case.calculated["valor_restituivel"] = format_money(refundable)
                if returned:
                    case.calculated["valor_ja_reembolsado"] = format_money(returned)
                # Art. 740, § 3º, of the Civil Code: the carrier may keep at most 5% of the fare; boarding taxes
                # (``taxas`` of the item) go back whole.
                taxes = sum((money(item.get("taxas")) or Decimal(0) for item in charged), Decimal(0))
                fare = paid - taxes
                kept = (fare * Decimal("0.05")).quantize(Decimal("0.01"))
                case.calculated["retencao_maxima_5"] = format_money(kept)
                case.calculated["restituicao_95"] = format_money(paid - kept - returned)
    if material is not None and "valor_material_terceiros" not in case.calculated:
        third = material - (refundable or 0)
        if third > 0:
            case.calculated["valor_material_terceiros"] = format_money(third)
    compensation = None
    if case.get("peca.usar_tese_pretericao"):
        compensation = _amount(case, "calc.des_valor_aprox")
        if compensation is None:
            compensation = _compensation(case)
            if compensation is not None:
                case.calculated["des_valor_aprox"] = format_money(compensation)
        if compensation is None:
            case.calculated["des_sem_cotacao"] = True
        if compensation is not None and authors > 1:
            case.calculated["des_valor_total"] = format_money(compensation * authors)
            compensation *= authors
    montreal = None
    if case.get("peca.usar_des_montreal"):
        units = case.get("processo.des_quantidade_montreal") or 1519
        case.calculated["des_montreal_quantidade"] = str(units)
        quote = _highest_quote(case)
        if quote is None:
            case.calculated["des_sem_cotacao"] = True
        else:
            montreal = (Decimal(str(units)) * quote).quantize(Decimal("0.01"))
            case.calculated["des_montreal_valor"] = format_money(montreal)
            if authors > 1:
                case.calculated["des_montreal_total"] = format_money(montreal * authors)
                montreal *= authors
    claimed = material
    if refundable is not None and case.get("peca.repeticao_em_dobro") is True:
        case.calculated["valor_material_dobro"] = format_money(refundable * 2)
        claimed = (material or refundable) + refundable
    if moral is not None:
        total = moral + (claimed or 0) + (compensation or 0) + (montreal or 0)
        case.calculated["valor_causa"] = format_money(total)
        case.calculated["valor_causa_extenso"] = money_in_words(total)
        ceiling = _amount(case, "escritorio.teto_juizado") or small_claims_ceiling(case.get("processo.data_peca"))
        if ceiling is not None:
            case.calculated["acima_do_teto"] = total > ceiling
            case.calculated["teto_juizado"] = format_money(ceiling)
    if case.get("viagem.data_voo_original"):
        case.calculated["voo_em_ou_apos_26_10_2026"] = _parse_date(case.get("viagem.data_voo_original")) >= dt.date(
            2026, 10, 26
        )
    _durations(case)
    passengers = case.get("viagem.numero_passageiros")
    if passengers not in (None, ""):
        try:
            passengers = int(passengers)
        except (TypeError, ValueError) as error:
            raise ValueError(f"viagem.numero_passageiros: número inválido {passengers!r}") from error
        case.calculated.setdefault("n_passageiros_extenso", _integer_words(passengers))
        case.calculated.setdefault("n_acompanhantes", str(passengers - 1))
        case.calculated.setdefault("n_acompanhantes_extenso", _integer_words(passengers - 1))
    if case.get("processo.data_peca"):
        case.calculated["data_peca_extenso"] = date_in_words(case.get("processo.data_peca"))
