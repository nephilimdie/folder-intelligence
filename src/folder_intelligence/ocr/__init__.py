"""OCR: strategie intercambiabili (Apple Vision, Tesseract, modello locale) e la loro catena."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .apple_vision_ocr import AppleVisionOcr
from .model_ocr import ModelOcr
from .ocr_chain import OcrChain
from .ocr_engine_kind import OcrEngineKind
from .ocr_result import OcrResult
from .ocr_strategy import OcrStrategy
from .tesseract_ocr import TesseractOcr

if TYPE_CHECKING:
    from ..ai import AIEngine


def build_ocr(kind: OcrEngineKind, ai: "AIEngine | None" = None, model: str = "") -> OcrChain:
    """Catena per il motore scelto; gli altri restano come riserva, nell'ordine più sensato."""
    apple, tesseract, by_model = AppleVisionOcr(), TesseractOcr(), ModelOcr(ai, model)
    order = {
        OcrEngineKind.AUTO: [apple, tesseract],
        OcrEngineKind.APPLE: [apple, tesseract],
        OcrEngineKind.TESSERACT: [tesseract, apple],
        OcrEngineKind.MODEL: [by_model, apple, tesseract],
    }[kind]
    return OcrChain(order)


__all__ = ["OcrChain", "OcrEngineKind", "OcrResult", "OcrStrategy", "build_ocr"]
