"""Cronologia dei comandi della chat, cifrata riga per riga (frecce ↑/↓)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable

from . import crypto
from .audit import app_home

PURPOSE = "history"
MAX_ENTRIES = 1000


def history_path() -> Path:
    return app_home() / "history.enc"


def load() -> list[str]:
    """Voci dalla più vecchia alla più recente (migra il vecchio file in chiaro)."""
    path = history_path()
    legacy = path.with_name("history")
    entries: list[str] = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                entries.append(crypto.decrypt_text(line, PURPOSE))
            except crypto.CryptoError:
                continue
    if legacy.exists():
        # formato prompt_toolkit FileHistory: righe "+testo", blocchi separati da commenti
        old: list[str] = []
        block: list[str] = []
        for line in legacy.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.startswith("+"):
                block.append(line[1:])
            elif block:
                old.append("\n".join(block))
                block = []
        if block:
            old.append("\n".join(block))
        entries = old + entries
        _rewrite(entries)
        crypto.secure_delete(legacy)
    return entries[-MAX_ENTRIES:]


def _rewrite(entries: Iterable[str]) -> None:
    path = history_path()
    data = "\n".join(crypto.encrypt_text(e, PURPOSE) for e in entries) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(data)
    os.replace(tmp, path)


def append(entry: str) -> None:
    path = history_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as f:
        f.write(crypto.encrypt_text(entry, PURPOSE) + "\n")


def prompt_toolkit_history():
    """Adattatore per prompt_toolkit."""
    from prompt_toolkit.history import History

    class EncryptedHistory(History):
        def load_history_strings(self):
            return reversed(load())  # prompt_toolkit vuole dalla più recente

        def store_string(self, string: str) -> None:
            append(string)

    return EncryptedHistory()
