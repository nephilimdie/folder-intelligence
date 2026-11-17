"""Quale motore OCR usare (impostazione "ocr_engine")."""

from __future__ import annotations

from enum import Enum


class OcrEngineKind(str, Enum):
    AUTO = "auto"            # Apple Vision se disponibile, altrimenti Tesseract
    APPLE = "apple"          # Apple Vision (Testo attivo di macOS, Neural Engine)
    TESSERACT = "tesseract"  # Tesseract (riserva, funziona ovunque)
    MODEL = "model"          # modello OCR locale via Ollama (es. glm-ocr)

    @classmethod
    def parse(cls, value: object) -> "OcrEngineKind":
        try:
            return cls(str(value).lower())
        except ValueError:
            return cls.AUTO
