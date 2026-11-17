"""Catena di motori OCR: usa il primo disponibile che trova testo (Composite della strategia)."""

from __future__ import annotations

from pathlib import Path

from .ocr_engine_kind import OcrEngineKind
from .ocr_result import OcrResult
from .ocr_strategy import OcrStrategy


class OcrChain(OcrStrategy):
    kind = OcrEngineKind.AUTO

    def __init__(self, strategies: list[OcrStrategy]) -> None:
        self.strategies = strategies
        self._usable: list[OcrStrategy] | None = None

    async def usable(self) -> list[OcrStrategy]:
        if self._usable is None:
            self._usable = [s for s in self.strategies if await s.available()]
        return self._usable

    async def available(self) -> bool:
        return bool(await self.usable())

    async def read(self, image: Path) -> OcrResult:
        last = OcrResult.empty(self.kind)
        for strategy in await self.usable():
            result = await strategy.read(image)
            if result.useful:
                return result
            last = result
        return last
