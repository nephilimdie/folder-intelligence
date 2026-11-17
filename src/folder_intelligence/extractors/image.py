"""Immagini: OCR e modello visivo tramite `VisualReader` (documento o foto)."""

from __future__ import annotations

from pathlib import Path

from .. import shell
from ..documents import VisualReading
from .base import ExtractContext, Extraction, Extractor

MAX_SIDE = 1600   # lato massimo: abbastanza per leggere uno scontrino, leggero per il modello visivo


class ImageExtractor(Extractor):
    name = "image"
    priority = 50
    extensions = frozenset({".jpg", ".jpeg", ".png", ".gif", ".heic", ".heif", ".webp", ".bmp",
                            ".tif", ".tiff"})

    async def extract(self, path: Path, ctx: ExtractContext) -> Extraction | None:
        meta = await self._size(path)
        img = await self._prepare(path, ctx)
        try:
            reading = await ctx.visual.read(img) if ctx.visual else VisualReading()
        finally:
            if img != path:
                img.unlink(missing_ok=True)
        meta.update(reading.metadata())
        engine = f"{self.name}+{reading.engine}" if reading.engine else self.name
        return Extraction(text=reading.text[: ctx.max_chars], description=reading.description, metadata=meta,
                          engine=engine)

    @staticmethod
    async def _size(path: Path) -> dict:
        if not shell.which("sips"):
            return {}
        r = await shell.run(["sips", "-g", "pixelWidth", "-g", "pixelHeight", str(path)], timeout=15)
        meta = {}
        for line in r.stdout.splitlines():
            if "pixelWidth" in line or "pixelHeight" in line:
                k, _sep, v = line.strip().partition(":")
                meta[k.strip()] = v.strip()
        return meta

    @staticmethod
    async def _prepare(path: Path, ctx: ExtractContext) -> Path:
        """Converte (HEIC, TIFF…) e ridimensiona in JPEG con `sips` per OCR e modello visivo."""
        if not shell.which("sips"):
            return path
        out = ctx.tmpdir / f"img-{abs(hash(str(path)))}.jpg"
        r = await shell.run(["sips", "-s", "format", "jpeg", "-Z", str(MAX_SIDE), str(path),
                             "--out", str(out)], timeout=60)
        return out if r.ok and out.exists() else path
