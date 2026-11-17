"""File di testo e codice."""

from __future__ import annotations

from pathlib import Path

from ..threads import run_blocking
from .base import ExtractContext, Extraction, Extractor


class TextExtractor(Extractor):
    name = "text"
    priority = 50
    extensions = frozenset({
        ".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".yaml", ".yml", ".xml", ".log",
        ".ini", ".cfg", ".conf", ".toml", ".tex", ".srt", ".vtt", ".py", ".js", ".ts", ".tsx",
        ".jsx", ".java", ".c", ".cpp", ".h", ".go", ".rs", ".rb", ".php", ".swift", ".kt",
        ".sh", ".sql", ".css", ".ics", ".vcf", ".eml",
    })

    async def extract(self, path: Path, ctx: ExtractContext) -> Extraction | None:
        def read() -> bytes:
            with open(path, "rb") as f:
                return f.read(ctx.max_chars * 2)

        raw = await run_blocking(read, timeout=30)
        if b"\x00" in raw[:4096]:
            return None  # binario travestito
        return Extraction(text=raw.decode("utf-8", errors="ignore")[: ctx.max_chars], engine=self.name)
