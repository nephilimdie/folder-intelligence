"""Memoria dell'assistente con strategie intercambiabili (Strategy Pattern).

- `inmemory`: solo per la sessione corrente;
- `json`: persistente e cifrata in ~/.folder_intelligence/memory.enc (default);
- `lightrag`: server HTTP compatibile LightRAG (con fallback su JSON se non raggiungibile).
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Any

from . import crypto
from .audit import app_home


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"\w+", text.lower()) if len(t) > 2}


class MemoryStrategy(ABC):
    @abstractmethod
    async def save(self, kind: str, text: str, meta: dict[str, Any] | None = None) -> bool: ...

    @abstractmethod
    async def query(self, text: str, limit: int = 5) -> list[dict[str, Any]]: ...

    async def recent(self, limit: int = 5) -> list[dict[str, Any]]:
        return []


class InMemoryStrategy(MemoryStrategy):
    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []

    async def save(self, kind: str, text: str, meta: dict[str, Any] | None = None) -> bool:
        self.items.append({
            "kind": kind, "text": text, "meta": meta or {},
            "created_at": datetime.now().isoformat(timespec="seconds"),
        })
        self._persist()
        return True

    async def query(self, text: str, limit: int = 5) -> list[dict[str, Any]]:
        q = _tokens(text)
        scored = []
        for i, item in enumerate(self.items):
            score = len(q & _tokens(item["text"]))
            if score:
                scored.append((score, i, item))
        scored.sort(key=lambda t: (-t[0], -t[1]))
        return [item for _, _, item in scored[:limit]]

    async def recent(self, limit: int = 5) -> list[dict[str, Any]]:
        return list(reversed(self.items[-limit:]))

    def _persist(self) -> None:
        pass


class JsonFileStrategy(InMemoryStrategy):
    """Memoria persistente cifrata (AES-GCM) in `memory.enc`."""

    PURPOSE = "memory"

    def __init__(self, path: Path | None = None) -> None:
        super().__init__()
        self.path = path or app_home() / "memory.enc"
        legacy = self.path.with_name("memory.json")
        if self.path.exists():
            self.items = json.loads(crypto.read_encrypted(self.path, self.PURPOSE))
        elif legacy.exists():
            try:
                self.items = json.loads(legacy.read_text())
            except (json.JSONDecodeError, OSError):
                self.items = []
            self._persist()
            crypto.secure_delete(legacy)

    def _persist(self) -> None:
        data = json.dumps(self.items[-1000:], ensure_ascii=False).encode()
        crypto.write_encrypted(self.path, data, self.PURPOSE)


class LightRAGStrategy(MemoryStrategy):
    """Usa le API LightRAG (`/documents/text`, `/query`); se il server non risponde usa JSON."""

    def __init__(self, base_url: str = "http://localhost:9621") -> None:
        self.base_url = base_url.rstrip("/")
        self.fallback = JsonFileStrategy()

    async def save(self, kind: str, text: str, meta: dict[str, Any] | None = None) -> bool:
        await self.fallback.save(kind, text, meta)
        import aiohttp

        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as s:
                async with s.post(f"{self.base_url}/documents/text",
                                  json={"text": f"[{kind}] {text}"}) as r:
                    return r.status < 300
        except Exception:
            return True  # salvato comunque nel fallback

    async def query(self, text: str, limit: int = 5) -> list[dict[str, Any]]:
        import aiohttp

        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as s:
                async with s.post(f"{self.base_url}/query",
                                  json={"query": text, "mode": "hybrid"}) as r:
                    if r.status == 200:
                        data = await r.json()
                        answer = data.get("response") if isinstance(data, dict) else str(data)
                        if answer:
                            return [{"kind": "lightrag", "text": answer, "meta": {}}]
        except Exception:
            pass
        return await self.fallback.query(text, limit)

    async def recent(self, limit: int = 5) -> list[dict[str, Any]]:
        return await self.fallback.recent(limit)


def create_memory(kind: str = "json") -> MemoryStrategy:
    kind = kind.lower()
    if kind == "inmemory":
        return InMemoryStrategy()
    if kind == "lightrag":
        return LightRAGStrategy()
    return JsonFileStrategy()
