"""Documenti Office/OpenDocument (zip + XML, nessuna dipendenza) e formati gestiti da textutil."""

from __future__ import annotations

import html
import re
import zipfile
from pathlib import Path

from .. import shell
from ..threads import run_blocking
from .base import ExtractContext, Extraction, Extractor


def _xml_text(xml: str, tag: str | None = None) -> str:
    if tag:
        parts = re.findall(rf"<{tag}[^>]*>(.*?)</{tag}>", xml, re.S)
        return " ".join(html.unescape(re.sub(r"<[^>]+>", "", p)) for p in parts)
    xml = re.sub(r"</(w:p|text:p|text:h|a:p)>", "\n", xml)
    return html.unescape(re.sub(r"<[^>]+>", " ", xml))


class OfficeExtractor(Extractor):
    name = "office"
    priority = 50
    extensions = frozenset({".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp", ".docm", ".xlsm"})

    async def extract(self, path: Path, ctx: ExtractContext) -> Extraction | None:
        return await run_blocking(self._extract_sync, path, ctx, timeout=60)

    def _extract_sync(self, path: Path, ctx: ExtractContext) -> Extraction | None:
        ext = path.suffix.lower()
        parts: list[str] = []
        meta: dict = {}
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            read = lambda n: z.read(n).decode("utf-8", errors="ignore")  # noqa: E731
            if ext in (".docx", ".docm"):
                for n in ["word/document.xml"] + [n for n in names if re.match(r"word/(header|footer)\d*\.xml", n)]:
                    if n in names:
                        parts.append(_xml_text(read(n)))
            elif ext in (".xlsx", ".xlsm"):
                if "xl/workbook.xml" in names:
                    sheets = re.findall(r'<sheet [^>]*name="([^"]+)"', read("xl/workbook.xml"))
                    meta["sheets"] = sheets
                    parts.append("Fogli: " + ", ".join(sheets))
                if "xl/sharedStrings.xml" in names:
                    parts.append(_xml_text(read("xl/sharedStrings.xml"), "t"))
            elif ext == ".pptx":
                slides = sorted((n for n in names if re.match(r"ppt/slides/slide\d+\.xml", n)),
                                key=lambda n: int(re.findall(r"\d+", n)[-1]))
                meta["slides"] = len(slides)
                for i, n in enumerate(slides, 1):
                    parts.append(f"[Slide {i}] " + _xml_text(read(n), "a:t"))
            else:  # OpenDocument
                if "content.xml" in names:
                    parts.append(_xml_text(read("content.xml")))
            if "docProps/core.xml" in names:
                core = read("docProps/core.xml")
                for k in ("title", "creator", "subject"):
                    m = re.search(rf"<dc:{k}>(.*?)</dc:{k}>", core)
                    if m and m.group(1).strip():
                        meta[k] = html.unescape(m.group(1).strip())
        text = re.sub(r"[ \t]+", " ", "\n".join(parts))
        text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
        return Extraction(text=text[: ctx.max_chars], metadata=meta, engine=self.name)


class TextutilExtractor(Extractor):
    """macOS `textutil`: .doc, .rtf, .rtfd, .html, .webarchive, .wordml, .odt (fallback)."""

    name = "textutil"
    priority = 55
    extensions = frozenset({".doc", ".rtf", ".rtfd", ".html", ".htm", ".webarchive", ".wordml"})

    async def available(self, ctx: ExtractContext) -> bool:
        return shell.which("textutil") is not None

    async def extract(self, path: Path, ctx: ExtractContext) -> Extraction | None:
        res = await shell.run(["textutil", "-convert", "txt", "-stdout", str(path)], timeout=60)
        if not res.ok or not res.stdout.strip():
            return None
        return Extraction(text=res.stdout.strip()[: ctx.max_chars], engine=self.name)
