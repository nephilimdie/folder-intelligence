"""Immagini: OCR (tesseract) + descrizione col modello visivo locale."""

from __future__ import annotations

from pathlib import Path

from .. import shell
from .base import VISION_PROMPT, ExtractContext, Extraction, Extractor

MAX_SIDE = 1280


class ImageExtractor(Extractor):
    name = "image"
    priority = 50
    extensions = frozenset({".jpg", ".jpeg", ".png", ".gif", ".heic", ".heif", ".webp", ".bmp",
                            ".tif", ".tiff"})

    async def extract(self, path: Path, ctx: ExtractContext) -> Extraction | None:
        img = await self._prepare(path, ctx)
        text = ""
        description = ""
        engine = self.name
        meta: dict = {}
        try:
            if shell.which("sips"):
                r = await shell.run(["sips", "-g", "pixelWidth", "-g", "pixelHeight", str(path)], timeout=15)
                for line in r.stdout.splitlines():
                    if "pixelWidth" in line or "pixelHeight" in line:
                        k, _, v = line.strip().partition(":")
                        meta[k.strip()] = v.strip()
            if shell.which("tesseract"):
                r = await shell.run(["tesseract", str(img), "-", "-l", ctx.ocr_lang, "--psm", "3"], timeout=90)
                text = r.stdout.strip() if len(r.stdout.strip()) > 15 else ""
                if text:
                    engine += "+ocr"
            if await ctx.vision_ok():
                try:
                    description = await ctx.ai.describe_image(img, VISION_PROMPT)  # type: ignore[union-attr]
                    engine += "+vision"
                except Exception:
                    pass
        finally:
            if img != path:
                img.unlink(missing_ok=True)
        return Extraction(text=text[: ctx.max_chars], description=description, metadata=meta, engine=engine)

    async def _prepare(self, path: Path, ctx: ExtractContext) -> Path:
        """Converte (HEIC, TIFF…) e ridimensiona in JPEG con `sips` per OCR e modello visivo."""
        if not shell.which("sips"):
            return path
        out = ctx.tmpdir / f"img-{abs(hash(str(path)))}.jpg"
        r = await shell.run(["sips", "-s", "format", "jpeg", "-Z", str(MAX_SIDE), str(path),
                             "--out", str(out)], timeout=60)
        return out if r.ok and out.exists() else path
