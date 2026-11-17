"""Archivi: elenco dei file contenuti (utile per cercare "lo zip con le foto del matrimonio")."""

from __future__ import annotations

import tarfile
import zipfile
from pathlib import Path

from ..threads import run_blocking
from .base import ExtractContext, Extraction, Extractor

MAX_ENTRIES = 300


class ArchiveExtractor(Extractor):
    name = "archive"
    priority = 50
    extensions = frozenset({".zip", ".tar", ".tgz", ".gz", ".bz2", ".xz"})

    async def extract(self, path: Path, ctx: ExtractContext) -> Extraction | None:
        return await run_blocking(self._extract_sync, path, timeout=60)

    def _extract_sync(self, path: Path) -> Extraction | None:
        names: list[str] = []
        if zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as z:
                names = z.namelist()
        elif tarfile.is_tarfile(path):
            with tarfile.open(path) as t:
                names = t.getnames()
        if not names:
            return None
        shown = names[:MAX_ENTRIES]
        more = f"\n… e altri {len(names) - MAX_ENTRIES} file" if len(names) > MAX_ENTRIES else ""
        return Extraction(text="Contenuto dell'archivio:\n" + "\n".join(shown) + more,
                          metadata={"entries": len(names)}, engine=self.name)
