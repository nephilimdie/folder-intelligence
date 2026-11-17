"""Filtri descritti a parole: "solo i pdf", "del 2024", "togli Downloads", "sopra 100 €"…"""

from __future__ import annotations

import re

from ..documents.amount import parse_amount
from .constants import TYPE_WORDS
from .filters import Filters

_NUM = r"(\d[\d.,']*)\s*(?:€|eur|euro|\$|usd|chf)?"
_ABOVE = re.compile(rf"\b(?:sopra|oltre|piu di|più di|superiori? a|maggiori? di|almeno|above|over|more than|"
                    rf"at least)\s+(?:i\s+|the\s+)?(?:€\s*)?{_NUM}")
_BELOW = re.compile(rf"\b(?:sotto|meno di|inferiori? a|minori? di|al massimo|below|under|less than|at most)\s+"
                    rf"(?:i\s+|the\s+)?(?:€\s*)?{_NUM}")
_BETWEEN = re.compile(rf"\b(?:tra|fra|between)\s+(?:€\s*)?{_NUM}\s+(?:e|and)\s+(?:€\s*)?{_NUM}")


def refine(filters: Filters, text: str) -> list[str]:
    """Aggiorna `filters`; restituisce cosa è cambiato ([] = niente di riconosciuto, ["reset"])."""
    from ..dates import query_dates
    from ..intents import normalize

    t = normalize(text)
    if re.search(r"\b(tutti|tutto|togli i filtri|senza filtri|reset|all of them|clear filters?|everything)\b", t):
        filters.__init__()  # type: ignore[misc]
        return ["reset"]
    changed = []
    types = [k for k, words in TYPE_WORDS.items() if any(w in t for w in words)]
    if types:
        filters.types = types
        changed.append("types")
    if _amounts(filters, t):
        changed.append("amount")
    specs = query_dates(text) if "amount" not in changed else []
    if specs:
        filters.year, filters.month = specs[0].start.year, specs[0].start.month
        changed.append("date")
    elif "amount" not in changed:
        m = re.search(r"\b(?:del|nel|dal|from|in|of)\s+((?:19|20)\d{2})\b", t) or re.search(r"\b((?:19|20)\d{2})\b", t)
        if m:
            filters.year, filters.month = int(m.group(1)), None
            changed.append("date")
    m = re.search(r"\b(?:togli|escludi|senza|tranne|remove|exclude|without|except)\s+(?:quelli\s+|those\s+)?"
                  r"(?:in|da|dalla cartella|from|in the)?\s*([\w~/.\- ]+?)\s*$", t)
    if m and "amount" not in changed:
        filters.exclude.append(m.group(1).strip())
        changed.append("exclude")
    m = re.search(r"\b(?:solo|soltanto|only)\s+(?:quelli\s+)?(?:in|nella cartella|from|in the)\s+([\w~/.\- ]+?)\s*$", t)
    if m and "exclude" not in changed:
        filters.only = [m.group(1).strip()]
        changed.append("only")
    m = re.search(r"\b(?:ordina|ordinali|sort)\b.*\b(data|date|recent|nome|name|alfabet|dimension|size|grand|"
                  r"pertinen|relevan|importo|amount)", t)
    if m:
        filters.sort = _sort_key(m.group(1))
        changed.append("sort")
    return changed


def _amounts(filters: Filters, t: str) -> bool:
    m = _BETWEEN.search(t)
    if m:
        filters.min_amount, filters.max_amount = parse_amount(m.group(1))[0], parse_amount(m.group(2))[0]
        return True
    above, below = _ABOVE.search(t), _BELOW.search(t)
    if above:
        filters.min_amount = parse_amount(above.group(1))[0]
    if below:
        filters.max_amount = parse_amount(below.group(1))[0]
    return bool(above or below)


def _sort_key(word: str) -> str:
    if word in ("data", "date", "recent"):
        return "date"
    if word in ("nome", "name", "alfabet"):
        return "name"
    if word in ("dimension", "size", "grand"):
        return "size"
    return "relevance"
