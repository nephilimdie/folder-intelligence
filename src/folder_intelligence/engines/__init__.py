"""Motori AI intercambiabili (strategy pattern). Vedi base.py per crearne uno nuovo."""

from __future__ import annotations

from typing import Any

from .base import EMBED, TEXT, VISION, EngineStrategy, EngineUnsupported, parse_json
from .llamacpp import LlamaCppEngine
from .mlx import MlxEngine
from .ollama import OllamaEngine
from .remote import RemoteEngine

ENGINES: dict[str, type[EngineStrategy]] = {
    OllamaEngine.name: OllamaEngine,
    LlamaCppEngine.name: LlamaCppEngine,
    MlxEngine.name: MlxEngine,
    RemoteEngine.name: RemoteEngine,
}


def create_strategy(name: str, **kwargs: Any) -> EngineStrategy:
    cls = ENGINES.get(name)
    if cls is None:
        raise ValueError(f"motore sconosciuto '{name}' (disponibili: {', '.join(ENGINES)})")
    if cls is not OllamaEngine:
        kwargs.pop("base_url", None)
    if cls is not RemoteEngine:
        kwargs.pop("server_url", None)
    return cls(**kwargs)


__all__ = ["ENGINES", "create_strategy", "EngineStrategy", "EngineUnsupported", "parse_json",
           "OllamaEngine", "LlamaCppEngine", "MlxEngine", "RemoteEngine", "TEXT", "VISION", "EMBED"]
