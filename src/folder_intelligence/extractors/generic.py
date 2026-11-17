"""Ultima spiaggia per qualsiasi file: tipo rilevato da `file`."""

from __future__ import annotations

from ..i18n import _

from pathlib import Path

from .. import shell
from .base import ExtractContext, Extraction, Extractor


class GenericExtractor(Extractor):
    name = "generic"
    priority = 99

    def supports(self, path: Path) -> bool:
        return True

    async def extract(self, path: Path, ctx: ExtractContext) -> Extraction | None:
        kind = await shell.file_type(path)
        return Extraction(text=_("Tipo di file: {kind}", kind=kind) if kind else "", metadata={"file_type": kind},
                          engine=self.name)
