"""Che tipo di immagine è: un documento (scontrino, scansione, schermata) o una foto."""

from __future__ import annotations

import re
from enum import Enum

MIN_DOCUMENT_WORDS = 8   # sotto: foto con qualche scritta (insegne, magliette), non un documento


class ImageKind(str, Enum):
    DOCUMENT = "document"
    PHOTO = "photo"

    @classmethod
    def from_ocr(cls, text: str) -> "ImageKind":
        words = re.findall(r"[^\W\d_]{2,}|\d+[.,]?\d*", text or "")
        return cls.DOCUMENT if len(words) >= MIN_DOCUMENT_WORDS else cls.PHOTO
