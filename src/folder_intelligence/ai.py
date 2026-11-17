"""Motore AI dell'app: facciata (Context dello strategy pattern) sopra uno o più motori.

Tre capacità, ognuna servita da una strategia (vedi `engines/`):
- testo     → ragionamento, JSON, router dei comandi (default qwen2.5:14b)
- immagini  → descrizione/lettura immagini (default qwen3.5:9b)
- embedding → vettori per la ricerca semantica (default bge-m3)

Esempio (predefinito se llama.cpp è installato e i modelli GGUF ci sono):
    testo + embedding con llama.cpp dentro l'app, immagini con Ollama.

Il resto del codice usa solo questa facciata (`ai.generate_json`, `ai.describe_image`, …) e non
sa quale motore c'è sotto: cambiare motore = cambiare configurazione (`--engine`, `FI_ENGINE`).
"""

from __future__ import annotations

from .i18n import _

import os
import time
from pathlib import Path
from typing import Any

from .classifier import DEFAULT_OLLAMA_MODEL
from .engines import ENGINES, create_strategy
from .engines.base import EMBED, TEXT, VISION, EngineStrategy, EngineUnsupported
from .engines.ollama import DEFAULT_URL as DEFAULT_OLLAMA_URL

DEFAULT_VISION_MODEL = os.environ.get("FI_VISION_MODEL", "qwen3.5:9b")
DEFAULT_EMBED_MODEL = os.environ.get("FI_EMBED_MODEL", "bge-m3")
DEFAULT_ENGINE = os.environ.get("FI_ENGINE", "auto")
DEFAULT_VISION_ENGINE = os.environ.get("FI_VISION_ENGINE", "auto")
DEFAULT_EMBED_ENGINE = os.environ.get("FI_EMBED_ENGINE", "auto")
AVAILABILITY_CACHE_SECONDS = 30
LOW_MEMORY_GB = 32


def total_memory_gb() -> float:
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2 ** 30
    except (ValueError, OSError, AttributeError):
        return 0.0


class AIEngine:
    """Facciata: inoltra ogni richiesta alla strategia scelta per quella capacità."""

    def __init__(self, text: EngineStrategy | None, vision: EngineStrategy | None = None,
                 embed: EngineStrategy | None = None) -> None:
        self.strategies: dict[str, EngineStrategy | None] = {TEXT: text, VISION: vision, EMBED: embed}
        self._cache: dict[str, tuple[float, bool]] = {}

    # ------------------------------------------------------------ descrizione
    def strategy(self, capability: str) -> EngineStrategy | None:
        return self.strategies.get(capability)

    @property
    def model(self) -> str:
        s = self.strategy(TEXT)
        return s.model if s else ""

    @property
    def vision_model(self) -> str:
        s = self.strategy(VISION)
        return s.vision_model if s else ""

    @property
    def embed_model(self) -> str:
        s = self.strategy(EMBED)
        return s.embed_model if s else ""

    def engine_of(self, capability: str) -> str:
        s = self.strategy(capability)
        return s.label if s else "—"

    def summary(self) -> str:
        """'qwen2.5:14b (llama.cpp) · immagini qwen3.5:9b (Ollama) · ricerca bge-m3 (llama.cpp)'"""
        parts = []
        for cap, label in ((TEXT, ""), (VISION, _("immagini") + " "), (EMBED, _("ricerca") + " ")):
            s = self.strategy(cap)
            if s:
                parts.append(f"{label}{s.model_for(cap)} ({s.label})")
        return " · ".join(parts)

    def describe(self) -> dict[str, Any]:
        return {cap: ({"engine": s.name, "label": s.label, "model": s.model_for(cap)} if s else None)
                for cap, s in self.strategies.items()}

    def why_unavailable(self, capability: str = TEXT) -> str:
        s = self.strategy(capability)
        return s.why_unavailable(capability) if s else _("nessun motore configurato")

    # ------------------------------------------------------------ disponibilità
    async def _available(self, capability: str) -> bool:
        cached = self._cache.get(capability)
        if cached and time.monotonic() - cached[0] < AVAILABILITY_CACHE_SECONDS:
            return cached[1]
        s = self.strategy(capability)
        try:
            ok = bool(s) and await s.available(capability)  # type: ignore[union-attr]
        except Exception:
            ok = False
        self._cache[capability] = (time.monotonic(), ok)
        return ok

    async def is_available(self) -> bool:
        return await self._available(TEXT)

    async def can_see(self) -> bool:
        return await self._available(VISION)

    async def can_embed(self) -> bool:
        return await self._available(EMBED)

    def _need(self, capability: str) -> EngineStrategy:
        s = self.strategy(capability)
        if s is None:
            raise EngineUnsupported(_("nessun motore configurato per: {capability}", capability=capability))
        return s

    # ------------------------------------------------------------ operazioni
    async def generate_json(self, prompt: str, system: str = "", schema: dict[str, Any] | None = None,
                            model: str | None = None) -> dict[str, Any]:
        return await self._need(TEXT).generate_json(prompt, system=system, schema=schema, model=model)

    async def chat(self, messages: list[dict[str, str]], model: str | None = None) -> str:
        return await self._need(TEXT).chat(messages, model=model)

    async def describe_image(self, image: Path | bytes, prompt: str, as_json: bool = False) -> Any:
        return await self._need(VISION).describe_image(image, prompt, as_json=as_json)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return await self._need(EMBED).embed(texts)

    async def release(self, capability: str) -> None:
        """Libera il modello di una capacità (es. il modello di testo prima di leggere le immagini)."""
        s = self.strategy(capability)
        if s is not None:
            import asyncio

            await asyncio.to_thread(s.release, capability)

    def swap_text_and_vision(self) -> bool:
        """True se conviene non tenere in memoria insieme modello di testo e modello visivo:
        motori diversi (es. llama.cpp + Ollama) su un Mac con poca memoria."""
        text, vision = self.strategy(TEXT), self.strategy(VISION)
        if text is None or vision is None:
            return False
        if text is vision:  # un solo motore (es. Ollama): gestisce da sé la memoria
            return False
        forced = os.environ.get("FI_LOW_MEMORY")
        if forced is not None:
            return forced not in ("0", "", "false")
        return total_memory_gb() < LOW_MEMORY_GB

    def close(self) -> None:
        for s in {id(s): s for s in self.strategies.values() if s}.values():
            s.close()


def _pick(requested: str, capability: str, model: str) -> str:
    """'auto' → llama.cpp se installato e il modello GGUF è disponibile, altrimenti Ollama."""
    requested = (requested or "auto").lower()
    if requested != "auto":
        if requested not in ENGINES:
            raise ValueError(_("motore sconosciuto '{name}' (disponibili: {names})", name=requested, names=", ".join(ENGINES)))
        return requested
    if capability in (TEXT, EMBED):
        from .engines.llamacpp import LlamaCppEngine, resolve_gguf

        if LlamaCppEngine.installed() and resolve_gguf(model):
            return "llamacpp"
    return "ollama"


def create_ai(engine: str = DEFAULT_ENGINE, vision_engine: str = DEFAULT_VISION_ENGINE,
              embed_engine: str = DEFAULT_EMBED_ENGINE, model: str = DEFAULT_OLLAMA_MODEL,
              vision_model: str = DEFAULT_VISION_MODEL, embed_model: str = DEFAULT_EMBED_MODEL,
              ollama_url: str = DEFAULT_OLLAMA_URL) -> AIEngine:
    """Costruisce la facciata scegliendo una strategia per capacità (una istanza per motore)."""
    # se l'utente sceglie un motore preciso (es. --engine ollama), vale anche per gli embedding
    embed_req = embed_engine if (embed_engine or "auto") != "auto" else engine
    names = {TEXT: _pick(engine, TEXT, model),
             VISION: _pick(vision_engine, VISION, vision_model),
             EMBED: _pick(embed_req, EMBED, embed_model)}
    instances: dict[str, EngineStrategy] = {}
    for name in set(names.values()):
        instances[name] = create_strategy(name, model=model, vision_model=vision_model,
                                          embed_model=embed_model, base_url=ollama_url)
    return AIEngine(instances[names[TEXT]], instances[names[VISION]], instances[names[EMBED]])


__all__ = ["AIEngine", "create_ai", "DEFAULT_VISION_MODEL", "DEFAULT_EMBED_MODEL", "DEFAULT_OLLAMA_URL",
           "DEFAULT_ENGINE", "DEFAULT_VISION_ENGINE", "DEFAULT_EMBED_ENGINE"]
