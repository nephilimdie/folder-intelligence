"""Autocompletamento per la chat: percorsi di cartelle e frasi di comando.

La logica è pura (nessuna dipendenza da terminale) e viene adattata sia a
prompt_toolkit sia a readline in `cli.py`; è riusata anche dall'API (/complete).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from .i18n import _
from .intents import HOME_ALIASES

# Frasi proposte quando si scrive all'inizio della riga, per lingua (vedi i18n).
COMMANDS_BY_LANG = {
    "it": [
        "analizza la cartella ", "trovami i duplicati", "cancella i duplicati", "trova foto simili",
        "trova elementi simili", "trova documenti simili", "classifica i file", "che file ci sono?",
        "organizza i file", "organizza in ", "mostrami i 10 file più grandi", "cerca ", "riepilogo",
        "esporta il report in md", "esporta il report in json", "esporta il report in csv",
        "annulla l'ultima operazione", "ricordami di ", "cosa ricordi?", "vai in ", "aiuto", "esci",
    ],
    "en": [
        "analyze the folder ", "find duplicates", "delete the duplicates", "find similar photos",
        "find similar items", "find similar documents", "classify the files", "what files are there?",
        "organize the files", "organize into ", "show me the 10 largest files", "search ", "summary",
        "export the report as md", "export the report as json", "export the report as csv",
        "undo the last operation", "remind me to ", "what do you remember?", "go to ", "help", "exit",
    ],
}


def commands() -> list[str]:
    from .i18n import lang

    return COMMANDS_BY_LANG.get(lang(), COMMANDS_BY_LANG["en"])


COMMANDS = COMMANDS_BY_LANG["it"]  # compatibilità

# Parole dopo le quali ha senso proporre una cartella anche senza "/" iniziale.
FOLDER_TRIGGERS = {"cartella", "folder", "in", "nella", "dentro", "analizza", "scansiona", "vai",
                   "into", "inside", "to", "analyze", "analyse", "scan", "directory"}

MAX_RESULTS = 50


@dataclass(frozen=True)
class Completion:
    text: str            # testo che sostituisce il token
    start: int           # quanti caratteri prima del cursore vengono sostituiti (negativo)
    display: str         # cosa mostrare nel menu
    meta: str = ""       # descrizione breve (es. "cartella", "comando")


def escape_path(path: str) -> str:
    return path.replace("\\", "\\\\").replace(" ", "\\ ")


def unescape_path(path: str) -> str:
    return re.sub(r"\\(.)", r"\1", path)


def current_token(text: str) -> str:
    """Ultimo token prima del cursore; gli spazi preceduti da backslash non lo spezzano."""
    m = re.search(r"((?:\\.|[^\s\\])*)$", text)
    return m.group(1) if m else ""


def _list_dirs(base: Path, prefix: str, include_hidden: bool) -> list[str]:
    try:
        entries = sorted(os.scandir(base), key=lambda e: e.name.lower())
    except OSError:
        return []
    out: list[str] = []
    pl = prefix.lower()
    for e in entries:
        if not include_hidden and e.name.startswith(".") and not prefix.startswith("."):
            continue
        try:
            is_dir = e.is_dir()
        except OSError:
            continue
        if is_dir and e.name.lower().startswith(pl):
            out.append(e.name)
            if len(out) >= MAX_RESULTS:
                break
    return out


def complete_path(token: str, cwd: Path, home: Path | None = None) -> list[Completion]:
    """Completa `token` (es. '~/Doc', '/Us', 'sub/a', 'My\\ Fo') con le sole cartelle."""
    home = home or Path.home()
    raw = unescape_path(token)
    head, _sep, prefix = raw.rpartition("/")
    if "/" in raw:
        head_dir = head + "/" if head else "/"
    else:
        head_dir = ""

    if head_dir.startswith("~"):
        base = Path(str(home) + head_dir[1:])
    elif head_dir.startswith("/"):
        base = Path(head_dir)
    else:
        base = cwd / head_dir if head_dir else cwd

    if raw == "~":
        return [Completion("~/", -len(token), "~/", "home")]

    result = []
    for name in _list_dirs(base, prefix, include_hidden=prefix.startswith(".")):
        full = f"{head_dir}{name}/"
        result.append(Completion(escape_path(full), -len(token), name + "/", _("cartella")))
    return result


def complete(text_before_cursor: str, cwd: Path, home: Path | None = None) -> list[Completion]:
    """Completamenti per la riga corrente."""
    home = home or Path.home()
    token = current_token(text_before_cursor)
    before = text_before_cursor[: len(text_before_cursor) - len(token)]
    prev_words = re.findall(r"\w+", before.lower())
    prev = prev_words[-1] if prev_words else ""

    # 1) percorso esplicito
    if token.startswith(("~", "/", "./", "../")) or "/" in token:
        return complete_path(token, cwd, home)

    # 2) inizio riga → frasi di comando
    if not before.strip():
        low = text_before_cursor.lower()
        return [Completion(c, -len(text_before_cursor), c.strip(), _("comando"))
                for c in commands() if c.startswith(low) and c != low]

    # 3) dopo "cartella"/"in"/... → alias (Downloads, Scrivania…) + sottocartelle
    if prev in FOLDER_TRIGGERS:
        out: list[Completion] = []
        tl = token.lower()
        seen: set[str] = set()
        for alias, real in HOME_ALIASES.items():
            if real and alias.startswith(tl) and (home / real).is_dir() and real not in seen:
                seen.add(real)
                out.append(Completion(real, -len(token), real, f"~/{real}"))
        out += complete_path(token, cwd, home)
        if token:
            out += [c for c in complete_path("~/" + token, cwd, home) if c.display.rstrip("/") not in seen]
        return out[:MAX_RESULTS]

    # 4) altrove: le frasi intere che iniziano come la riga scritta
    low = text_before_cursor.lower()
    return [Completion(c, -len(text_before_cursor), c.strip(), _("comando"))
            for c in commands() if c.startswith(low) and c != low]


def prompt_toolkit_completer(get_cwd):
    """Adattatore prompt_toolkit (usato da chat classica e interfaccia a pannelli)."""
    from prompt_toolkit.completion import Completer
    from prompt_toolkit.completion import Completion as PTCompletion

    class _Completer(Completer):
        def get_completions(self, document, complete_event):
            for c in complete(document.text_before_cursor, get_cwd()):
                yield PTCompletion(c.text, start_position=c.start, display=c.display, display_meta=c.meta)

    return _Completer()
