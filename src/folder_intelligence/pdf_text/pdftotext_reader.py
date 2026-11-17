"""Testo dei PDF con pdftotext (Poppler): veloce, mantiene le colonne delle tabelle."""

from __future__ import annotations

from pathlib import Path

from .. import shell
from .pdf_text_strategy import PdfTextStrategy


class PdftotextReader(PdfTextStrategy):
    name = "pdftotext"

    async def available(self) -> bool:
        return shell.which("pdftotext") is not None

    async def read(self, path: Path, max_pages: int) -> str:
        r = await shell.run(["pdftotext", "-l", str(max_pages), "-q", "-layout", str(path), "-"], timeout=60)
        return r.stdout if r.ok else ""
