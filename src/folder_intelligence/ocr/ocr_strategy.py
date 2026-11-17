"""Strategy pattern per l'OCR: ogni motore legge il testo di un'immagine allo stesso modo."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from .ocr_engine_kind import OcrEngineKind
from .ocr_result import OcrResult


class OcrStrategy(ABC):
    kind: OcrEngineKind

    @abstractmethod
    async def available(self) -> bool:
        """True se il motore può lavorare ora (libreria, programma o modello presenti)."""

    @abstractmethod
    async def read(self, image: Path) -> OcrResult:
        """Testo dell'immagine (vuoto se non c'è testo o il motore fallisce: mai eccezioni)."""
