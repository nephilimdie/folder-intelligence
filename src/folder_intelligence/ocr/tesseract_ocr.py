"""OCR con Tesseract: riserva quando Apple Vision non c'è."""

from __future__ import annotations

from pathlib import Path

from .. import shell
from .ocr_engine_kind import OcrEngineKind
from .ocr_result import OcrResult
from .ocr_strategy import OcrStrategy


class TesseractOcr(OcrStrategy):
    kind = OcrEngineKind.TESSERACT

    def __init__(self, languages: str = "ita+eng", timeout: float = 120) -> None:
        self.languages = languages
        self.timeout = timeout

    async def available(self) -> bool:
        return shell.which("tesseract") is not None

    async def read(self, image: Path) -> OcrResult:
        r = await shell.run(["tesseract", str(image), "-", "-l", self.languages, "--psm", "3"], timeout=self.timeout)
        return OcrResult(r.stdout.strip() if r.ok else "", self.kind)
