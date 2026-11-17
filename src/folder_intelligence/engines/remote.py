"""Motore "remoto": usa i modelli già caricati dal server API locale (folder-intelligence --serve).

Evita di avere due copie dei modelli in memoria quando chat e server (o l'app Mac) sono aperti
insieme: la chat inoltra testo, immagini ed embedding al server via HTTP su 127.0.0.1, con il
token dell'API. Con `--engine auto` viene scelto da solo se il server risponde.
"""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.request
from pathlib import Path
from typing import Any

from .base import EMBED, TEXT, VISION, EngineStrategy

def default_server() -> str:
    return os.environ.get("FI_SERVER_URL", "http://127.0.0.1:8765")
STATUS_CACHE_SECONDS = 10


def _token() -> str | None:
    env = os.environ.get("FI_API_TOKEN")
    if env:
        return env
    try:
        from ..audit import app_home

        return (app_home() / "api_token").read_text().strip() or None
    except OSError:
        return None


class RemoteEngine(EngineStrategy):
    name = "remote"
    label = "server locale"
    supports_text = supports_vision = supports_embeddings = True

    def __init__(self, model: str | None = None, vision_model: str | None = None,
                 embed_model: str | None = None, server_url: str | None = None,
                 timeout: float = 300.0, **options: Any) -> None:
        super().__init__(model, vision_model, embed_model, **options)
        self.server_url = (server_url or default_server()).rstrip("/")
        self.timeout = timeout
        self._status: dict[str, Any] | None = None
        self._status_at = 0.0

    # ---------------------------------------------------------------- scoperta
    @classmethod
    def reachable(cls, server_url: str | None = None, timeout: float = 0.4) -> bool:
        """True se sulla porta risponde il *nostro* server, con il token e l'AI attiva."""
        if os.environ.get("FI_USE_SERVER", "1") == "0" or not _token():
            return False
        server_url = server_url or default_server()
        try:
            with urllib.request.urlopen(f"{server_url.rstrip('/')}/health", timeout=timeout) as r:
                if json.loads(r.read()).get("service") != "folder-intelligence":
                    return False
        except Exception:
            return False
        return bool(cls(server_url=server_url).status().get("text"))

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None,
                 timeout: float | None = None) -> dict[str, Any]:
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(f"{self.server_url}{path}", data=data, method=method,
                                     headers={"Authorization": f"Bearer {_token() or ''}",
                                              "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout or self.timeout) as r:
            return json.loads(r.read())

    def status(self) -> dict[str, Any]:
        if self._status is None or time.monotonic() - self._status_at > STATUS_CACHE_SECONDS:
            try:
                self._status = self._request("GET", "/ai/status", timeout=3)
            except Exception:
                self._status = {}
            self._status_at = time.monotonic()
            engines = self._status.get("engines") or {}
            for cap, attr in ((TEXT, "model"), (VISION, "vision_model"), (EMBED, "embed_model")):
                if engines.get(cap):
                    setattr(self, attr, engines[cap].get("model", ""))
        return self._status

    async def _call(self, path: str, payload: dict[str, Any]) -> Any:
        import asyncio

        return (await asyncio.to_thread(self._request, "POST", path, payload))["result"]

    # ---------------------------------------------------------------- stato
    async def available(self, capability: str = TEXT) -> bool:
        import asyncio

        return bool((await asyncio.to_thread(self.status)).get(capability))

    def why_unavailable(self, capability: str = TEXT) -> str:
        return f"il server locale ({self.server_url}) non offre '{capability}': controlla che sia avviato"

    def prefers_phases(self) -> bool:
        """Il server decide se alternare testo e immagini (poca memoria, motori diversi)."""
        return bool(self.status().get("swap"))

    def describe(self) -> dict[str, Any]:
        return {**super().describe(), "url": self.server_url, "server": self.status().get("summary")}

    def release(self, capability: str) -> None:
        try:
            self._request("POST", "/ai/release", {"capability": capability}, timeout=30)
        except Exception:
            pass

    # ---------------------------------------------------------------- operazioni
    async def generate_json(self, prompt: str, system: str = "", schema: dict[str, Any] | None = None,
                            model: str | None = None) -> dict[str, Any]:
        result = await self._call("/ai/generate_json", {"prompt": prompt, "system": system,
                                                        "json_schema": schema, "model": model})
        return result if isinstance(result, dict) else {}

    async def chat(self, messages: list[dict[str, str]], model: str | None = None) -> str:
        return str(await self._call("/ai/chat", {"messages": messages, "model": model}))

    async def describe_image(self, image: Path | bytes, prompt: str, as_json: bool = False) -> Any:
        raw = image.read_bytes() if isinstance(image, Path) else image
        return await self._call("/ai/describe_image", {"image_b64": base64.b64encode(raw).decode(),
                                                       "prompt": prompt, "as_json": as_json})

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return await self._call("/ai/embed", {"texts": texts})
