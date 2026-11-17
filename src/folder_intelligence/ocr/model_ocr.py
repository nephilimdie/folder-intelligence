"""OCR con un modello locale specializzato (es. glm-ocr via Ollama): utile per scansioni difficili."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from .ocr_engine_kind import OcrEngineKind
from .ocr_result import OcrResult
from .ocr_strategy import OcrStrategy

if TYPE_CHECKING:
    from ..ai import AIEngine

PROMPT = ("Transcribe all the text in this image exactly as written, keeping the line breaks and the "
          "table rows. Output only the text, nothing else.")


class ModelOcr(OcrStrategy):
    kind = OcrEngineKind.MODEL

    def __init__(self, ai: "AIEngine | None", model: str) -> None:
        self.ai = ai
        self.model = model

    async def available(self) -> bool:
        return bool(self.ai and self.model and await self.ai.has_model(self.model))

    async def read(self, image: Path) -> OcrResult:
        try:
            text = await self.ai.describe_image(image, PROMPT, model=self.model)  # type: ignore[union-attr]
        except Exception:
            return OcrResult.empty(self.kind)
        return OcrResult(str(text or "").strip(), self.kind)
