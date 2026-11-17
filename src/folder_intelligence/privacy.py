"""Rimozione di dati personali dai testi che escono dall'app (es. verso la memoria MCP).

Copre dati strutturati (email, telefoni, IBAN, codice fiscale, P.IVA, carte, targhe,
numeri lunghi, URL). NON riconosce in modo affidabile nomi di persona: per questo la
modalità di default verso l'MCP non invia descrizioni libere (vedi mcp_memory.py).
"""

from __future__ import annotations

import re

PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("email", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
    ("url", re.compile(r"https?://\S+")),
    ("iban", re.compile(r"\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){3,7}(?:\s?[A-Z0-9]{1,4})?\b")),
    ("codice_fiscale", re.compile(r"\b[A-Z]{6}\d{2}[A-EHLMPR-T]\d{2}[A-Z]\d{3}[A-Z]\b", re.I)),
    ("carta", re.compile(r"\b(?:\d[ -]?){13,19}\b")),
    ("telefono", re.compile(r"(?<!\w)(?:\+|00)?\d[\d\s./-]{7,}\d\b")),
    ("partita_iva", re.compile(r"\b(?:IT)?\d{11}\b")),
    ("targa", re.compile(r"\b[A-Z]{2}\s?\d{3}\s?[A-Z]{2}\b")),
    ("numero", re.compile(r"\b\d{6,}\b")),
]


def redact(text: str) -> str:
    for label, pattern in PATTERNS:
        text = pattern.sub(f"[{label}]", text)
    return text
