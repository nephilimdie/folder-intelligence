"""PDF: testo nativo (pdftotext, docling per le tabelle); scansioni → OCR e modello visivo."""

from __future__ import annotations

import re
from pathlib import Path

from .. import shell
from ..pdf_text import read_pdf_text
from .base import ExtractContext, Extraction, Extractor

OCR_PAGES = 3
SCANNED_BELOW_CHARS = 80


class PdfExtractor(Extractor):
    name = "pdf"
    priority = 50
    extensions = frozenset({".pdf"})

    async def available(self, ctx: ExtractContext) -> bool:
        return shell.which("pdftotext") is not None

    async def extract(self, path: Path, ctx: ExtractContext) -> Extraction | None:
        meta = await self._info(path)
        text, reader = await read_pdf_text(path, ctx.deep)
        engine = self.name if reader in ("", "pdftotext") else f"{self.name}+{reader}"
        description = ""
        if len(text.strip()) < SCANNED_BELOW_CHARS and ctx.visual:  # probabilmente scansionato
            text, description, engine = await self._read_scan(path, ctx, meta)
        if not text.strip() and not description:
            return Extraction(metadata=meta, engine=engine) if meta else None
        return Extraction(text=text[: ctx.max_chars], description=description, metadata=meta, engine=engine)

    async def _read_scan(self, path: Path, ctx: ExtractContext, meta: dict) -> tuple[str, str, str]:
        """OCR delle prime pagine; il modello visivo guarda solo la prima (descrizione e campi)."""
        pages = await self._render(path, ctx)
        texts, description, engine = [], "", f"{self.name}+scan"
        try:
            for i, page in enumerate(pages):
                reading = await ctx.visual.read(page, describe=i == 0)  # type: ignore[union-attr]
                texts.append(reading.text)
                if i == 0:
                    description = reading.description
                    meta.update(reading.metadata())
                    engine = f"{self.name}+{reading.engine}" if reading.engine else engine
        finally:
            for page in pages:
                page.unlink(missing_ok=True)
        return "\n\n".join(t for t in texts if t), description, engine

    @staticmethod
    async def _info(path: Path) -> dict:
        info = await shell.run(["pdfinfo", str(path)], timeout=15)
        meta = {}
        for key in ("Pages", "Title", "Author", "Producer", "CreationDate"):
            m = re.search(rf"^{key}:\s*(.+)$", info.stdout, re.M)
            if m:
                meta[key.lower()] = m.group(1).strip()
        return meta

    @staticmethod
    async def _render(path: Path, ctx: ExtractContext) -> list[Path]:
        if not shell.which("pdftoppm"):
            return []
        prefix = ctx.tmpdir / f"pdf-{abs(hash(str(path)))}"
        await shell.run(["pdftoppm", "-r", "150", "-png", "-f", "1", "-l", str(OCR_PAGES),
                         str(path), str(prefix)], timeout=120)
        return sorted(ctx.tmpdir.glob(prefix.name + "*.png"))
