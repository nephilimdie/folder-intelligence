"""Una combinazione di motori e modelli da provare nel banco (DTO)."""

from __future__ import annotations

from dataclasses import dataclass

from ..ocr import OcrEngineKind


@dataclass(frozen=True)
class BenchConfig:
    name: str
    ocr: OcrEngineKind = OcrEngineKind.AUTO
    vision_model: str = "qwen3.5:9b"
    document_model: str = ""          # "" = per i documenti si usa il modello visivo generale
    summary_model: str = "qwen2.5:7b"
    ocr_model: str = "glm-ocr"

    def models(self) -> list[str]:
        """Modelli Ollama che servono (per saltare le combinazioni non installate)."""
        needed = [self.vision_model, self.summary_model, self.document_model]
        if self.ocr is OcrEngineKind.MODEL:
            needed.append(self.ocr_model)
        return [m for m in needed if m]
