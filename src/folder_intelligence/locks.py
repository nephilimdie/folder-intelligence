"""Lock tra processi sull'indice cifrato (un solo scrittore alla volta).

L'indice vive in memoria e viene riscritto per intero su disco: se chat e servizio in background
lo tenessero aperto insieme, l'ultimo a salvare cancellerebbe il lavoro dell'altro. Quindi:

- la chat (e il server API) prendono il lock all'avvio e lo tengono finché restano aperti;
  prima di aspettarlo creano `index.wanted`, così il servizio in background finisce il file in
  corso, salva e lo lascia subito;
- il servizio in background lo prende solo per un piccolo blocco di file e solo se è libero.
"""

from __future__ import annotations

import fcntl
import os
import time
from pathlib import Path

from .audit import app_home


def lock_path() -> Path:
    return app_home() / "index.lock"


def wanted_path() -> Path:
    return app_home() / "index.wanted"


class IndexLock:
    def __init__(self) -> None:
        self._fd: int | None = None

    def acquire(self, blocking: bool = True, timeout: float | None = None,
                on_wait=None) -> bool:
        path = lock_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._fd = fd
            return True
        except BlockingIOError:
            if not blocking:
                os.close(fd)
                return False
        # occupato (servizio in background al lavoro): lo avvisiamo e aspettiamo che salvi
        wanted_path().touch()
        if on_wait:
            on_wait()
        deadline = time.monotonic() + timeout if timeout else None
        try:
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    self._fd = fd
                    return True
                except BlockingIOError:
                    if deadline and time.monotonic() > deadline:
                        os.close(fd)
                        return False
                    time.sleep(0.2)
        finally:
            wanted_path().unlink(missing_ok=True)

    def release(self) -> None:
        if self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            finally:
                os.close(self._fd)
                self._fd = None

    @property
    def held(self) -> bool:
        return self._fd is not None


def app_wants_index() -> bool:
    """True se la chat o il server stanno aspettando l'indice (il servizio deve fermarsi)."""
    return wanted_path().exists()
