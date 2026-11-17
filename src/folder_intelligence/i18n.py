"""Traduzioni (stile gettext): il testo italiano nel codice fa da chiave.

    from .i18n import _, _n
    _("Nessun file duplicato in {root}.", root=path)
    _n("{n} file spostato", "{n} file spostati", n)

- lingua: `--lang` / `FI_LANG`, altrimenti la lingua di macOS (AppleLanguages), altrimenti
  `LANG`; lingue supportate: SUPPORTED; default inglese per lingue non supportate;
- traduzioni in `locales/<lingua>.json` ({"testo italiano": "traduzione"}); l'italiano è la
  lingua sorgente e non ha bisogno di un file;
- per aggiungere una lingua: crea `locales/<codice>.json` e aggiungi il codice a SUPPORTED.
  `tests/test_i18n.py` verifica che ogni testo del codice sia tradotto.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from contextvars import ContextVar
from functools import lru_cache
from pathlib import Path
from typing import Any

SOURCE = "it"
SUPPORTED = ("it", "en")
DEFAULT = "en"
LANGUAGE_NAMES = {"it": "italiano", "en": "English"}
_LOCALES = Path(__file__).with_name("locales")
_current: str | None = None
# lingua per richiesta (API: header Accept-Language); ha la precedenza su quella del processo
_request_lang: ContextVar[str | None] = ContextVar("fi_request_lang", default=None)


def _macos_languages() -> list[str]:
    if sys.platform != "darwin":
        return []
    try:
        out = subprocess.run(["defaults", "read", "-g", "AppleLanguages"], capture_output=True,
                             text=True, timeout=2).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return re.findall(r'"?([A-Za-z]{2,3})(?:[-_][A-Za-z0-9]+)*"?\s*,?\s*\n', out)


def _normalize(code: str | None) -> str | None:
    if not code:
        return None
    code = code.strip().lower().replace("_", "-").split("-")[0].split(".")[0]
    return code or None


def detect() -> str:
    """FI_LANG → lingua di macOS → LANG → inglese."""
    forced = _normalize(os.environ.get("FI_LANG"))
    if forced:
        return forced if forced in SUPPORTED else DEFAULT
    for code in _macos_languages():
        code = _normalize(code)
        if code in SUPPORTED:
            return code  # type: ignore[return-value]
    env = _normalize(os.environ.get("LC_ALL") or os.environ.get("LC_MESSAGES") or os.environ.get("LANG"))
    if env in SUPPORTED:
        return env  # type: ignore[return-value]
    return DEFAULT


def lang() -> str:
    global _current
    req = _request_lang.get()
    if req:
        return req
    if _current is None:
        _current = detect()
    return _current


def set_language(code: str | None) -> str:
    """Imposta la lingua (es. da --lang); None = rileva di nuovo."""
    global _current
    code = _normalize(code)
    _current = (code if code in SUPPORTED else DEFAULT) if code else detect()
    return _current


def from_accept_language(header: str | None) -> str | None:
    """'en-US,en;q=0.9,it;q=0.8' → prima lingua supportata, in ordine di preferenza."""
    if not header:
        return None
    prefs = []
    for i, part in enumerate(header.split(",")):
        code, _sep, q = part.strip().partition(";q=")
        try:
            weight = float(q) if q else 1.0
        except ValueError:
            weight = 0.0
        prefs.append((-weight, i, _normalize(code)))
    for _w, _i, code in sorted(prefs):
        if code in SUPPORTED:
            return code
    return None


def use_request_language(code: str | None):
    """Imposta la lingua per il contesto corrente (una richiesta API); restituisce il token per reset."""
    return _request_lang.set(code if code in SUPPORTED else None)


def reset_request_language(token) -> None:
    _request_lang.reset(token)


def language_name(code: str | None = None) -> str:
    return LANGUAGE_NAMES.get(code or lang(), code or lang())


@lru_cache(maxsize=None)
def catalog(code: str) -> dict[str, str]:
    if code == SOURCE:
        return {}
    try:
        return json.loads((_LOCALES / f"{code}.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _(msg: str, **kw: Any) -> str:
    """Traduce `msg` nella lingua corrente e sostituisce i segnaposto {nome}."""
    text = catalog(lang()).get(msg, msg) if lang() != SOURCE else msg
    if kw:
        try:
            return text.format(**kw)
        except (KeyError, IndexError, ValueError):
            return msg.format(**kw)
    return text


def _n(singular: str, plural: str, n: int, **kw: Any) -> str:
    """Singolare/plurale: `_n("{n} file spostato", "{n} file spostati", n)`."""
    return _(singular if n == 1 else plural, n=n, **kw)


QUESTION_MARKERS = ("(sì/no)", "(yes/no)")


def is_question(line: str) -> bool:
    """True se la riga è una domanda sì/no (in qualsiasi lingua supportata)."""
    return any(m in line for m in QUESTION_MARKERS)


def strip_question_marker(line: str) -> str:
    for m in QUESTION_MARKERS:
        line = line.replace(" " + m, "").replace(m, "")
    return line.strip()


def N_(msg: str) -> str:
    """Segna un testo da tradurre senza tradurlo subito (tabelle, costanti): tradurlo con _()."""
    return msg


__all__ = ["_", "_n", "N_", "lang", "set_language", "detect", "language_name", "SUPPORTED",
           "from_accept_language", "use_request_language", "reset_request_language",
           "is_question", "strip_question_marker", "QUESTION_MARKERS"]
