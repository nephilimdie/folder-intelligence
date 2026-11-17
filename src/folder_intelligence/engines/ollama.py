"""Motore Ollama (servizio HTTP locale, http://localhost:11434)."""

from __future__ import annotations

import base64
import os
import time
from pathlib import Path
from typing import Any

from .base import EMBED, TEXT, VISION, EngineStrategy, parse_json

DEFAULT_URL = os.environ.get("FI_OLLAMA_URL", "http://localhost:11434")
MODELS_CACHE_SECONDS = 30


class OllamaEngine(EngineStrategy):
    name = "ollama"
    label = "Ollama"
    supports_text = True
    supports_vision = True
    supports_embeddings = True

    def __init__(self, model: str | None = None, vision_model: str | None = None,
                 embed_model: str | None = None, base_url: str = DEFAULT_URL,
                 timeout: float = 180.0, **options: Any) -> None:
        super().__init__(model, vision_model, embed_model, **options)
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._models: set[str] | None = None
        self._models_at = 0.0

    # ---------------------------------------------------------------- stato
    async def models(self) -> set[str]:
        if self._models is None or time.monotonic() - self._models_at > MODELS_CACHE_SECONDS:
            import aiohttp

            try:
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=3)) as s:
                    async with s.get(f"{self.base_url}/api/tags") as r:
                        data = await r.json() if r.status == 200 else {}
                self._models = {m.get("name", "") for m in data.get("models", [])}
            except Exception:
                self._models = set()
            self._models_at = time.monotonic()
        return self._models

    async def has(self, model: str | None) -> bool:
        if not model:
            return False
        names = await self.models()
        return model in names or f"{model}:latest" in names

    async def available(self, capability: str = TEXT) -> bool:
        return self.supports(capability) and await self.has(self.model_for(capability))

    def why_unavailable(self, capability: str = TEXT) -> str:
        model = self.model_for(capability)
        return f"avvia `ollama serve` e scarica il modello con `ollama pull {model}`"

    def describe(self) -> dict[str, Any]:
        return {**super().describe(), "url": self.base_url}

    async def _post(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        import aiohttp

        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self.timeout)) as s:
            async with s.post(f"{self.base_url}{endpoint}", json=payload) as r:
                r.raise_for_status()
                return await r.json()

    # ---------------------------------------------------------------- testo
    async def generate_json(self, prompt: str, system: str = "", schema: dict[str, Any] | None = None,
                            model: str | None = None) -> dict[str, Any]:
        data = await self._post("/api/generate", {
            "model": model or self.model, "prompt": prompt, "system": system,
            "format": schema or "json", "stream": False, "think": False,
            "options": {"temperature": 0},
        })
        return parse_json(data.get("response", ""))

    async def chat(self, messages: list[dict[str, str]], model: str | None = None) -> str:
        data = await self._post("/api/chat", {"model": model or self.model, "messages": messages,
                                              "stream": False, "think": False})
        return data.get("message", {}).get("content", "").strip()

    # ---------------------------------------------------------------- immagini
    async def describe_image(self, image: Path | bytes, prompt: str, as_json: bool = False) -> Any:
        raw = image.read_bytes() if isinstance(image, Path) else image
        payload: dict[str, Any] = {
            "model": self.vision_model, "prompt": prompt, "stream": False, "think": False,
            "images": [base64.b64encode(raw).decode()], "options": {"temperature": 0},
        }
        if as_json:
            payload["format"] = "json"
        text = (await self._post("/api/generate", payload)).get("response", "").strip()
        return parse_json(text) if as_json else text

    # ---------------------------------------------------------------- vettori
    async def embed(self, texts: list[str]) -> list[list[float]]:
        data = await self._post("/api/embed", {"model": self.embed_model, "input": [t[:8000] for t in texts]})
        return data.get("embeddings", [])


__all__ = ["OllamaEngine", "DEFAULT_URL", "EMBED", "VISION"]
