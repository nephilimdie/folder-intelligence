"""Testo dei PDF con docling: struttura del documento e tabelle in Markdown (più lento).

Si usa solo nella fase di comprensione e solo per PDF con tabelle; serve il pacchetto `docling`.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from ..threads import run_blocking
from .pdf_text_strategy import PdfTextStrategy


def _convert(path: str, max_pages: int) -> str:
    from docling.document_converter import DocumentConverter

    result = DocumentConverter().convert(path, max_num_pages=max_pages)
    return result.document.export_to_markdown()


class DoclingReader(PdfTextStrategy):
    name = "docling"

    async def available(self) -> bool:
        try:
            return importlib.util.find_spec("docling") is not None
        except (ImportError, ValueError):
            return False

    async def read(self, path: Path, max_pages: int) -> str:
        try:
            return await run_blocking(_convert, str(path), max_pages, timeout=300)
        except Exception:
            return ""
