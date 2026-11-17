"""Servizio esterno document-intelligence (OCR Tesseract + layout docling + modello visivo).

Attivo solo se configurato:
  FI_DOCINTEL_URL   es. http://localhost:18081
  FI_DOCINTEL_TOKEN token di accesso del servizio
Se il servizio non risponde si ricade sugli estrattori locali.
"""

from __future__ import annotations

import base64
import mimetypes
import os
from pathlib import Path
from typing import Any

from .base import ExtractContext, Extraction, Extractor

MAX_BYTES = 20 * 1024 * 1024


def _collect(obj: Any, key_part: str, out: list[str]) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if key_part in k.lower() and isinstance(v, str) and v.strip():
                out.append(v.strip())
            else:
                _collect(v, key_part, out)
    elif isinstance(obj, list):
        for v in obj:
            _collect(v, key_part, out)


class DocumentIntelligenceExtractor(Extractor):
    name = "document-intelligence"
    priority = 10
    extensions = frozenset({".pdf", ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp"})

    def __init__(self) -> None:
        self.url = os.environ.get("FI_DOCINTEL_URL", "").rstrip("/")
        self.token = os.environ.get("FI_DOCINTEL_TOKEN", "")
        self._healthy: bool | None = None

    async def available(self, ctx: ExtractContext) -> bool:
        if not (self.url and self.token):
            return False
        if self._healthy is None:
            import aiohttp

            try:
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=3)) as s:
                    async with s.get(f"{self.url}/health") as r:
                        self._healthy = r.status == 200
            except Exception:
                self._healthy = False
        return self._healthy

    async def extract(self, path: Path, ctx: ExtractContext) -> Extraction | None:
        if path.stat().st_size > MAX_BYTES:
            return None
        import aiohttp

        payload = {
            "filename": path.name,
            "mime_type": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
            "content_base64": base64.b64encode(path.read_bytes()).decode(),
            "context": {},
        }
        headers = {"Authorization": f"Bearer {self.token}"}
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=300)) as s:
                async with s.post(f"{self.url}/v1/analyze", json=payload, headers=headers) as r:
                    if r.status != 200:
                        return None
                    data = await r.json()
        except Exception:
            return None
        text = str(data.get("plain_text") or "")
        if not text:
            parts: list[str] = []
            _collect(data.get("pages", []), "text", parts)
            text = "\n".join(dict.fromkeys(parts))
        descriptions: list[str] = []
        _collect(data, "description", descriptions)
        return Extraction(text=text[: ctx.max_chars],
                          description="\n".join(dict.fromkeys(descriptions))[:4000],
                          metadata={"pages": len(data.get("pages", []))} if data.get("pages") else {},
                          engine=self.name)
