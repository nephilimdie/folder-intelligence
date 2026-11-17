"""Lettura di un'immagine o di una pagina scansionata (DTO)."""

from __future__ import annotations

from dataclasses import dataclass

from .document_fields import DocumentFields
from .image_kind import ImageKind


@dataclass(frozen=True)
class VisualReading:
    text: str = ""                      # testo letto con l'OCR
    description: str = ""               # cosa mostra (modello visivo)
    kind: ImageKind = ImageKind.PHOTO
    fields: DocumentFields = DocumentFields()
    engine: str = ""                    # es. "apple+vision:qwen3-vl:8b"

    def metadata(self) -> dict:
        data: dict = {"image_kind": self.kind.value}
        if not self.fields.empty:
            data["fields"] = self.fields.to_dict()
        return data
