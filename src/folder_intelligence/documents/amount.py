"""Importi scritti come capita ("1.234,56 €", "EUR 12.50", "€ 1,234.00") → numero e valuta."""

from __future__ import annotations

import re

CURRENCIES = {"€": "EUR", "eur": "EUR", "euro": "EUR", "$": "USD", "usd": "USD", "£": "GBP", "gbp": "GBP",
              "chf": "CHF", "fr.": "CHF"}
_NUMBER = re.compile(r"-?\d{1,3}(?:[.,' ]\d{3})*(?:[.,]\d{1,2})?|-?\d+(?:[.,]\d{1,2})?")


def parse_amount(value: object) -> tuple[float | None, str | None]:
    """(importo, valuta ISO) oppure (None, None)."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value), None
    text = str(value or "").strip().lower()
    match = _NUMBER.search(text)
    if not match:
        return None, None
    currency = next((iso for sym, iso in CURRENCIES.items() if sym in text), None)
    return _to_float(match.group(0)), currency


def _to_float(raw: str) -> float | None:
    s = raw.replace(" ", "").replace("'", "")
    if "," in s and "." in s:  # l'ultimo separatore è quello dei decimali
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    elif "," in s:
        head, _sep, tail = s.rpartition(",")
        s = head.replace(",", "") + ("." + tail if len(tail) <= 2 else tail)
    elif s.count(".") > 1 or (s.count(".") == 1 and len(s.rpartition(".")[2]) == 3):
        s = s.replace(".", "")  # "1.234" = milleduecentotrentaquattro
    try:
        return float(s)
    except ValueError:
        return None
