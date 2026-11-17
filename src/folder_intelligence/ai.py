"""Motore AI locale (Ollama): testo, visione ed embedding.

Tre modelli distinti, ciascuno opzionale:
- `model`        → ragionamento/JSON (default qwen2.5:14b)
- `vision_model` → descrizione/lettura immagini (default qwen3.5:9b)
- `embed_model`  → vettori per la ricerca semantica (default bge-m3)
"""

from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Any

from .classifier import DEFAULT_OLLAMA_MODEL, DEFAULT_OLLAMA_URL, OllamaClient, _parse_json

DEFAULT_VISION_MODEL = os.environ.get("FI_VISION_MODEL", "qwen3.5:9b")
DEFAULT_EMBED_MODEL = os.environ.get("FI_EMBED_MODEL", "bge-m3")


class AIEngine(OllamaClient):
    def __init__(self, base_url: str = DEFAULT_OLLAMA_URL, model: str = DEFAULT_OLLAMA_MODEL,
                 vision_model: str = DEFAULT_VISION_MODEL, embed_model: str = DEFAULT_EMBED_MODEL,
                 timeout: float = 180.0):
        super().__init__(base_url, model, timeout)
        self.vision_model = vision_model
        self.embed_model = embed_model
        self._models: set[str] | None = None

    async def models(self) -> set[str]:
        if self._models is None:
            import aiohttp

            try:
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=3)) as s:
                    async with s.get(f"{self.base_url}/api/tags") as r:
                        data = await r.json() if r.status == 200 else {}
                self._models = {m.get("name", "") for m in data.get("models", [])}
            except Exception:
                self._models = set()
        return self._models

    async def has(self, model: str | None) -> bool:
        if not model:
            return False
        names = await self.models()
        return model in names or f"{model}:latest" in names

    async def is_available(self) -> bool:  # modello testuale
        return await self.has(self.model)

    async def can_see(self) -> bool:
        return await self.has(self.vision_model)

    async def can_embed(self) -> bool:
        return await self.has(self.embed_model)

    async def describe_image(self, image: Path | bytes, prompt: str, as_json: bool = False) -> Any:
        """Chiede al modello visivo di descrivere/leggere un'immagine (PNG/JPEG)."""
        import aiohttp

        raw = image.read_bytes() if isinstance(image, Path) else image
        payload: dict[str, Any] = {
            "model": self.vision_model, "prompt": prompt, "stream": False, "think": False,
            "images": [base64.b64encode(raw).decode()], "options": {"temperature": 0},
        }
        if as_json:
            payload["format"] = "json"
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self.timeout)) as s:
            async with s.post(f"{self.base_url}/api/generate", json=payload) as r:
                r.raise_for_status()
                data = await r.json()
        text = data.get("response", "").strip()
        return _parse_json(text) if as_json else text

    async def embed(self, texts: list[str]) -> list[list[float]]:
        import aiohttp

        payload = {"model": self.embed_model, "input": [t[:8000] for t in texts]}
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self.timeout)) as s:
            async with s.post(f"{self.base_url}/api/embed", json=payload) as r:
                r.raise_for_status()
                data = await r.json()
        return data.get("embeddings", [])
