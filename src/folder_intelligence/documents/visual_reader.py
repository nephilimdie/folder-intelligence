"""Legge un'immagine: OCR, poi il modello giusto per il tipo di immagine.

- documento (scontrino, scansione, schermata con testo) → modello per documenti (es. qwen3-vl),
  che restituisce descrizione e campi (data, importo, emittente…);
- foto → modello visivo generale, che la descrive.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ..ocr import OcrEngineKind, OcrResult, OcrStrategy
from .document_fields import DocumentFields
from .image_kind import ImageKind
from .prompts import document_prompt, photo_prompt
from .visual_reading import VisualReading

if TYPE_CHECKING:
    from ..ai import AIEngine


class VisualReader:
    def __init__(self, ocr: OcrStrategy | None, ai: "AIEngine | None", document_model: str = "",
                 use_vision: bool = True) -> None:
        self.ocr = ocr
        self.ai = ai
        self.document_model = document_model
        self.use_vision = use_vision
        self._document_model_ok: bool | None = None

    async def read(self, image: Path, describe: bool = True) -> VisualReading:
        """OCR sempre; con `describe` anche il modello visivo (descrizione e campi)."""
        ocr = await self.ocr.read(image) if self.ocr else OcrResult.empty(OcrEngineKind.AUTO)
        text = ocr.text if ocr.useful else ""
        kind = ImageKind.from_ocr(text)
        engine = ocr.engine.value if text else ""
        if not (describe and self.use_vision and self.ai and await self.ai.can_see()):
            return VisualReading(text, "", kind, DocumentFields(), engine)
        try:
            if kind is ImageKind.DOCUMENT:
                model = await self._model_for_documents()
                data = await self.ai.describe_image(image, document_prompt(text), as_json=True, model=model)
                description, fields = str(data.get("description") or ""), DocumentFields.from_dict(data.get("fields"))
                engine += f"+vision:{model or self.ai.vision_model}"
            else:
                description, fields = str(await self.ai.describe_image(image, photo_prompt())), DocumentFields()
                engine += f"+vision:{self.ai.vision_model}"
        except Exception:
            description, fields = "", DocumentFields()
        return VisualReading(text, description.strip(), kind, fields, engine.lstrip("+"))

    async def _model_for_documents(self) -> str | None:
        """Il modello per documenti se è installato; altrimenti quello visivo generale (None)."""
        if self._document_model_ok is None:
            self._document_model_ok = bool(self.document_model and self.ai
                                           and await self.ai.has_model(self.document_model))
        return self.document_model if self._document_model_ok else None
