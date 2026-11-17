"""Date nel linguaggio naturale (italiano e inglese).

- `query_dates(text)`: date citate in una ricerca ("31/03/2024", "marzo 2024", "03-2024",
  "March 2024") → intervallo + frasi da cercare nel testo/nome dei documenti;
- `parse_due(text)`: scadenza di un promemoria ("domani", "venerdì", "il 20 novembre",
  "tra 3 giorni", "on Nov 20") → (data, testo senza la data).
"""

from __future__ import annotations

import calendar
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, timedelta

MONTHS = {
    # italiano
    "gennaio": 1, "febbraio": 2, "marzo": 3, "aprile": 4, "maggio": 5, "giugno": 6, "luglio": 7,
    "agosto": 8, "settembre": 9, "ottobre": 10, "novembre": 11, "dicembre": 12,
    "gen": 1, "feb": 2, "mar": 3, "apr": 4, "mag": 5, "giu": 6, "lug": 7, "ago": 8, "set": 9,
    "ott": 10, "nov": 11, "dic": 12,
    # inglese
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6, "july": 7,
    "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "dec": 12,
}
MONTH_NAMES_IT = ["", "gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto",
                  "settembre", "ottobre", "novembre", "dicembre"]
MONTH_NAMES_EN = ["", "january", "february", "march", "april", "may", "june", "july", "august",
                  "september", "october", "november", "december"]
WEEKDAYS = {"lunedi": 0, "martedi": 1, "mercoledi": 2, "giovedi": 3, "venerdi": 4, "sabato": 5, "domenica": 6,
            "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6}
_MONTH_RE = "|".join(sorted(MONTHS, key=len, reverse=True))


def _norm(text: str) -> str:
    t = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in t if not unicodedata.combining(c))


@dataclass
class DateSpec:
    start: date
    end: date
    kind: str                                   # "day" | "month"
    phrases: list[str] = field(default_factory=list)   # da cercare come frase nel testo (FTS)
    name_patterns: list[str] = field(default_factory=list)  # da cercare nel nome del file
    matched: str = ""                           # il pezzo di frase riconosciuto


def _day(y: int, m: int, d: int, matched: str) -> DateSpec | None:
    try:
        dt = date(y, m, d)
    except ValueError:
        return None
    dd, mm = f"{d:02d}", f"{m:02d}"
    return DateSpec(dt, dt, "day", matched=matched,
                    phrases=[f"{dd} {mm} {y}", f"{y} {mm} {dd}", f"{d} {MONTH_NAMES_IT[m]} {y}",
                             f"{d} {MONTH_NAMES_EN[m]} {y}", f"{MONTH_NAMES_EN[m]} {d} {y}"],
                    name_patterns=[f"{dd}-{mm}-{y}", f"{y}-{mm}-{dd}", f"{y}{mm}{dd}", f"{dd}{mm}{y}",
                                   f"{y}.{mm}.{dd}", f"{dd}.{mm}.{y}", f"{dd}_{mm}_{y}", f"{y}_{mm}_{dd}"])


def _month(y: int, m: int, matched: str) -> DateSpec | None:
    if not 1 <= m <= 12:
        return None
    mm = f"{m:02d}"
    last = calendar.monthrange(y, m)[1]
    return DateSpec(date(y, m, 1), date(y, m, last), "month", matched=matched,
                    phrases=[f"{mm} {y}", f"{y} {mm}", f"{MONTH_NAMES_IT[m]} {y}", f"{MONTH_NAMES_EN[m]} {y}"],
                    name_patterns=[f"{mm}-{y}", f"{y}-{mm}", f"{mm}_{y}", f"{y}_{mm}", f"{y}{mm}",
                                   f"{MONTH_NAMES_IT[m]}_{y}", f"{MONTH_NAMES_IT[m]} {y}",
                                   f"{MONTH_NAMES_EN[m]}_{y}", f"{MONTH_NAMES_EN[m]} {y}"])


def query_dates(text: str) -> list[DateSpec]:
    """Date esplicite in una richiesta di ricerca (giorno o mese+anno)."""
    t = _norm(text)
    found: list[DateSpec] = []
    taken: list[tuple[int, int]] = []

    def free(m: re.Match) -> bool:
        return not any(a < m.end() and m.start() < b for a, b in taken)

    patterns = [
        # 31/03/2024 · 31-03-2024 · 31.03.2024
        (r"\b(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})\b", lambda m: _day(int(m[3]), int(m[2]), int(m[1]), m[0])),
        # 2024-03-31
        (r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", lambda m: _day(int(m[1]), int(m[2]), int(m[3]), m[0])),
        # 31 marzo 2024 · 31 march 2024
        (rf"\b(\d{{1,2}})\s+({_MONTH_RE})\.?\s+(\d{{4}})\b", lambda m: _day(int(m[3]), MONTHS[m[2]], int(m[1]), m[0])),
        # march 31, 2024
        (rf"\b({_MONTH_RE})\.?\s+(\d{{1,2}}),?\s+(\d{{4}})\b", lambda m: _day(int(m[3]), MONTHS[m[1]], int(m[2]), m[0])),
        # marzo 2024 · march 2024
        (rf"\b({_MONTH_RE})\.?\s+(?:del\s+|of\s+)?(\d{{4}})\b", lambda m: _month(int(m[2]), MONTHS[m[1]], m[0])),
        # 03/2024 · 03-2024 · 2024-03
        (r"\b(\d{1,2})[/\-](\d{4})\b", lambda m: _month(int(m[2]), int(m[1]), m[0])),
        (r"\b(\d{4})[/\-](\d{1,2})\b", lambda m: _month(int(m[1]), int(m[2]), m[0])),
    ]
    for pattern, build in patterns:
        for m in re.finditer(pattern, t):
            if not free(m):
                continue
            spec = build(m)
            if spec is not None:
                found.append(spec)
                taken.append((m.start(), m.end()))
    return found


def strip_dates(text: str, specs: list[DateSpec]) -> str:
    t = _norm(text)
    for s in specs:
        t = t.replace(s.matched, " ")
    return re.sub(r"\s+", " ", t).strip()


# --------------------------------------------------------------------- scadenze
_RELATIVE = {"oggi": 0, "today": 0, "domani": 1, "tomorrow": 1, "dopodomani": 2,
             "day after tomorrow": 2}


def parse_due(text: str, today: date | None = None) -> tuple[date | None, str]:
    """('ricordami di pagare l'affitto venerdì') → (data del prossimo venerdì, 'pagare l'affitto').

    Restituisce (None, testo) se non c'è nessuna data."""
    today = today or date.today()
    original = text
    t = _norm(text)

    def cut(m: re.Match) -> str:
        # rimuove la data dal testo originale (stesse posizioni: _norm non cambia le lunghezze
        # per i caratteri latini comuni) e le preposizioni rimaste ("entro", "il", "on", "by")
        s = original[: m.start()] + original[m.end():]
        s = re.sub(r"\s+(entro|per|il|lo|la|di|del|on|by|for|at|the)\s*$", "", s.strip(), flags=re.I)
        s = re.sub(r"^\s*(entro|per|il|on|by)\s+", "", s, flags=re.I)
        return re.sub(r"\s{2,}", " ", s).strip(" ,.")

    m = re.search(r"\b(day after tomorrow|dopodomani|domani|tomorrow|oggi|today)\b", t)
    if m:
        return today + timedelta(days=_RELATIVE[m[1]]), cut(m)
    m = re.search(r"\b(?:tra|fra|in)\s+(\d{1,3})\s+(giorni|giorno|days|day|settimane|settimana|weeks|week)\b", t)
    if m:
        n = int(m[1]) * (7 if m[2].startswith(("settiman", "week")) else 1)
        return today + timedelta(days=n), cut(m)
    m = re.search(r"\b(?:(?:il|la|on|next|prossimo|prossima)\s+)?(" + "|".join(WEEKDAYS) + r")\b", t)
    if m:
        delta = (WEEKDAYS[m[1]] - today.weekday()) % 7 or 7
        return today + timedelta(days=delta), cut(m)
    specs = [s for s in query_dates(text) if s.kind == "day"]
    if specs:
        mm = re.search(re.escape(specs[0].matched), t)
        return specs[0].start, cut(mm) if mm else text
    # "il 20 novembre" / "20 nov" / "nov 20" senza anno → prossima occorrenza
    m = re.search(rf"\b(\d{{1,2}})\s+({_MONTH_RE})\b|\b({_MONTH_RE})\.?\s+(\d{{1,2}})\b", t)
    if m:
        day, month = (int(m[1]), MONTHS[m[2]]) if m[1] else (int(m[4]), MONTHS[m[3]])
        for year in (today.year, today.year + 1):
            try:
                d = date(year, month, day)
            except ValueError:
                break
            if d >= today:
                return d, cut(m)
    m = re.search(r"\b(\d{1,2})[/.](\d{1,2})\b(?![/.]\d)", t)  # 20/11 senza anno
    if m:
        for year in (today.year, today.year + 1):
            try:
                d = date(year, int(m[2]), int(m[1]))
            except ValueError:
                break
            if d >= today:
                return d, cut(m)
    return None, original.strip()
