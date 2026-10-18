"""The author's legal representative: when one is needed, and whether the case facts agree with the petition.

A minor author, or one represented by someone else (mother or father, guardian, curator, a civil attorney-in-fact),
sues through that representative, and the petition usually says so. The representative comes in the case facts as
`author.representative {cpf, name, kind}`; these checks only compare the facts with the author's birth date and the
petition's text, and report what does not fit as review items. Petition phrases are a heuristic: when the reviewer
confirms the author acts in person anyway, `author.selfRepresented: true` silences that one check (never the age check).
"""

import re
from datetime import date

# Petition phrases that mean the author acts through someone else.
SIGNALS = (
    r"representad[oa]\s+(?:neste\s+ato\s+)?(?:legalmente\s+)?por",
    r"neste\s+ato\s+representad[oa]",
    r"assistid[oa]\s+por\s+(?:sua|seu)",
    r"menor\s+imp[uú]bere",
    r"menor\s+de\s+idade",
    r"absolutamente\s+incapaz",
    r"\bcurador[a]?\b",
    r"\bcuratela\b",
    r"\btutor[a]?\b",
    # Not "o representante legal deste/do INSS": every BPC petition names the defendant's representative.
    r"\brepresentante\s+legal\b(?!\s+(?:deste|desta|dele|dela|do\s+INSS|da\s+autarquia|do\s+r[ée]u))",
)
REPRESENTATIVE_KINDS = ("mae", "pai", "tutor", "curador", "procurador", "outro")


def representation_signals(text):
    """The petition phrases that indicate the author is represented (empty when none)."""
    found = []
    for pattern in SIGNALS:
        match = re.search(pattern, str(text or ""), re.I)
        if match:
            found.append(" ".join(match.group(0).split()).lower())
    return list(dict.fromkeys(found))


def parse_birth_date(value):
    """A birth date given as DD/MM/YYYY or YYYY-MM-DD."""
    text = str(value or "").strip()
    match = re.fullmatch(r"(\d{2})/(\d{2})/(\d{4})", text) or re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", text)
    if not match:
        raise ValueError("author.birthDate must be DD/MM/YYYY or YYYY-MM-DD")
    day, month, year = (match[1], match[2], match[3]) if "/" in text else (match[3], match[2], match[1])
    return date(int(year), int(month), int(day))


def age_on(birth, today):
    return today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))


def representation_problems(facts, petition_cpfs, signals, today=None):
    """Why the facts' representative (or its absence) does not fit the author's age or the petition."""
    author = facts.get("author") or {}
    representative = author.get("representative")
    problems = []
    if author.get("birthDate"):
        age = age_on(parse_birth_date(author["birthDate"]), today or date.today())
        if age < 18 and not representative:
            problems.append(f"The author is a minor ({age} years old) but no legal representative was given")
    if signals and not representative and author.get("selfRepresented") is not True:
        problems.append(
            "The petition indicates the author is represented (" + ", ".join(signals[:3]) + ") "
            "but no legal representative was given; add author.representative, or author.selfRepresented: true if "
            "the author acts in person"
        )
    if representative and petition_cpfs:
        cpf = re.sub(r"\D", "", str(representative.get("cpf") or ""))
        if cpf not in petition_cpfs:
            problems.append("The legal representative's CPF was not found among the petition's formatted CPFs")
    return problems
