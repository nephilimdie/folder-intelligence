"""Strumenti comuni per interpretare le frasi (normalizzazione, percorsi).

La scelta del comando è fatta dal registro dei comandi (cartella `commands/`): ogni
comando dichiara le proprie parole chiave/regex in `match()`.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

HOME_ALIASES = {
    "download": "Downloads", "downloads": "Downloads", "scaricati": "Downloads",
    "documenti": "Documents", "documents": "Documents",
    "scrivania": "Desktop", "desktop": "Desktop",
    "immagini": "Pictures", "foto": "Pictures", "pictures": "Pictures",
    "musica": "Music", "music": "Music",
    "filmati": "Movies", "video": "Movies", "movies": "Movies",
    "home": "",
}


@dataclass
class Intent:
    type: str
    path: Path | None = None
    target: Path | None = None
    args: dict[str, Any] = field(default_factory=dict)


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in text if not unicodedata.combining(c)).strip()


_PATH_RE = re.compile(
    r"""(?:"([^"]+)"|(?<!\w)'([^']+)'|(?<![\w/.~])((?:~|\.{1,2})?/(?:\\.|[^\s,;"'\\])*|~(?=\s|$)))"""
)
_FOLDER_WORD_RE = re.compile(
    r"\b(?:cartella|folder|directory|dir|nella|nelle|nei|in|su|sulla|dentro)\s+(?:la\s+|di\s+)?(?:cartella\s+)?([\w\-.]+)",
    re.I,
)


def extract_paths(text: str, home: Path | None = None) -> list[Path]:
    """Estrae percorsi espliciti (/..., ~/..., "...") e alias tipo 'cartella Download'."""
    home = home or Path.home()
    found: list[Path] = []
    for m in _PATH_RE.finditer(text):
        raw = re.sub(r"\\(.)", r"\1", next(g for g in m.groups() if g))  # "Foto\ Vacanze"
        if raw in ("/",) and not text.strip().startswith("/"):
            continue
        found.append(Path(raw).expanduser())
    for m in _FOLDER_WORD_RE.finditer(text):
        word = normalize(m.group(1))
        if word in HOME_ALIASES:
            p = home / HOME_ALIASES[word] if HOME_ALIASES[word] else home
            if p.is_dir() and p not in found:
                found.append(p)
    return found


YES = {"si", "s", "y", "yes", "ok", "okay", "conferma", "confermo", "procedi", "vai", "fallo", "certo", "esegui"}
NO = {"no", "n", "annulla", "stop", "lascia stare", "non farlo", "niente"}


def parse(text: str, home: Path | None = None) -> Intent:
    """Interpreta una frase scegliendo il comando adatto (vedi commands/)."""
    from .commands import registry

    return registry().parse(text, home)
