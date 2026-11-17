"""Testo dei PDF digitali: pdftotext sempre, docling per le tabelle nella fase di comprensione."""

from __future__ import annotations

from pathlib import Path

from .docling_reader import DoclingReader
from .pdf_text_strategy import PdfTextStrategy
from .pdftotext_reader import PdftotextReader
from .table_layout import keep_columns, looks_tabular

MAX_PAGES = 30
DOCLING_MAX_PAGES = 15


async def read_pdf_text(path: Path, deep: bool, fast: PdfTextStrategy | None = None,
                        structured: PdfTextStrategy | None = None) -> tuple[str, str]:
    """(testo, motore). Il passo veloce usa solo pdftotext; con `deep` i PDF con tabelle
    passano a docling se è installato (righe e colonne come tabelle Markdown)."""
    fast = fast or PdftotextReader()
    if not await fast.available():
        return "", ""
    raw = await fast.read(path, MAX_PAGES)
    text = keep_columns(raw)
    structured = structured or DoclingReader()
    if deep and looks_tabular(raw) and await structured.available():
        better = (await structured.read(path, DOCLING_MAX_PAGES)).strip()
        if len(better) > len(text) * 0.5:
            return better, structured.name
    return text, fast.name


__all__ = ["DoclingReader", "PdfTextStrategy", "PdftotextReader", "keep_columns", "looks_tabular", "read_pdf_text"]
