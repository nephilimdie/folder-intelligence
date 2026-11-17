"""Righe di tabella nel testo di pdftotext -layout: colonne separate da più spazi.

Comprimere tutti gli spazi (come si faceva prima) fonde le colonne: "01/09 Bonifico 120,00 1.340,50"
non dice più quale numero è l'importo e quale il saldo. Qui le colonne diventano " | ".
"""

from __future__ import annotations

import re

_GAP = re.compile(r"\S(?: {2,}|\t+)\S")
_SPLIT = re.compile(r" {2,}|\t+")
_SPACES = re.compile(r"[ \t]+")
MIN_TABLE_ROWS = 3


def is_table_row(line: str) -> bool:
    return len(_GAP.findall(line.strip())) >= 2


def keep_columns(text: str) -> str:
    """Righe di tabella con colonne " | ", tutte le altre con spazi compressi."""
    out = []
    for line in text.splitlines():
        stripped = line.strip()
        out.append(" | ".join(_SPLIT.split(stripped)) if is_table_row(stripped) else _SPACES.sub(" ", stripped))
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def looks_tabular(text: str) -> bool:
    """Almeno qualche riga con 3+ colonne: estratto conto, fattura con righe, tabella."""
    return sum(1 for line in text.splitlines() if is_table_row(line)) >= MIN_TABLE_ROWS
