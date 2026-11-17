"""Strategy pattern per il testo dei PDF digitali (non scansionati)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class PdfTextStrategy(ABC):
    name: str = ""

    @abstractmethod
    async def available(self) -> bool:
        """True se il programma o la libreria ci sono."""

    @abstractmethod
    async def read(self, path: Path, max_pages: int) -> str:
        """Testo delle prime `max_pages` pagine ("" se non riesce)."""
