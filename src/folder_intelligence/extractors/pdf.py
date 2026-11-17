"""PDF: testo nativo con pdftotext; se è una scansione → OCR (tesseract) e/o modello visivo."""

from __future__ import annotations

import re
from pathlib import Path

from .. import shell
from .base import VISION_PROMPT, ExtractContext, Extraction, Extractor

OCR_PAGES = 3


class PdfExtractor(Extractor):
    name = "pdf"
    priority = 50
    extensions = frozenset({".pdf"})

    async def available(self, ctx: ExtractContext) -> bool:
        return shell.which("pdftotext") is not None

    async def extract(self, path: Path, ctx: ExtractContext) -> Extraction | None:
        meta: dict = {}
        info = await shell.run(["pdfinfo", str(path)], timeout=15)
        for key in ("Pages", "Title", "Author", "Producer", "CreationDate"):
            m = re.search(rf"^{key}:\s*(.+)$", info.stdout, re.M)
            if m:
                meta[key.lower()] = m.group(1).strip()

        res = await shell.run(["pdftotext", "-l", "30", "-q", "-layout", str(path), "-"], timeout=60)
        text = re.sub(r"[ \t]+", " ", res.stdout).strip()
        engine = self.name
        description = ""

        if len(text) < 80:  # probabilmente scansionato
            pages = await self._render(path, ctx)
            if pages and shell.which("tesseract"):
                ocr = []
                for png in pages:
                    r = await shell.run(["tesseract", str(png), "-", "-l", ctx.ocr_lang], timeout=120)
                    ocr.append(r.stdout.strip())
                text = "\n\n".join(t for t in ocr if t)
                engine = "pdf+ocr"
            if pages and await ctx.vision_ok():
                try:
                    description = await ctx.ai.describe_image(pages[0], VISION_PROMPT)  # type: ignore[union-attr]
                    engine += "+vision"
                except Exception:
                    pass
            for p in pages:
                p.unlink(missing_ok=True)

        if not text and not description:
            return Extraction(metadata=meta, engine=engine) if meta else None
        return Extraction(text=text[: ctx.max_chars], description=description, metadata=meta, engine=engine)

    async def _render(self, path: Path, ctx: ExtractContext) -> list[Path]:
        if not shell.which("pdftoppm"):
            return []
        prefix = ctx.tmpdir / f"pdf-{abs(hash(str(path)))}"
        await shell.run(["pdftoppm", "-r", "150", "-png", "-f", "1", "-l", str(OCR_PAGES),
                         str(path), str(prefix)], timeout=120)
        return sorted(ctx.tmpdir.glob(prefix.name + "*.png"))
