"""Risultato di una lettura OCR (DTO)."""

from __future__ import annotations

from dataclasses import dataclass

from .ocr_engine_kind import OcrEngineKind

MIN_USEFUL_CHARS = 15  # sotto questa soglia è rumore (bordi, loghi), non testo


@dataclass(frozen=True)
class OcrResult:
    text: str
    engine: OcrEngineKind
    confidence: float | None = None   # 0..1 se il motore la fornisce

    @property
    def useful(self) -> bool:
        return len(self.text.strip()) >= MIN_USEFUL_CHARS

    @classmethod
    def empty(cls, engine: OcrEngineKind) -> "OcrResult":
        return cls("", engine)
