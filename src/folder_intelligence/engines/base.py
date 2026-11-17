"""Strategy pattern per i motori AI.

Ogni motore (Ollama, llama.cpp, MLX, …) è una `EngineStrategy` con la stessa interfaccia:

- testo:     `generate_json(prompt, system, schema, model)`, `chat(messages, model)`
- immagini:  `describe_image(image, prompt, as_json)`
- vettori:   `embed(texts)`

e dichiara cosa sa fare con `supports_text / supports_vision / supports_embeddings` e
`available(capability)`. La facciata `AIEngine` (ai.py) sceglie una strategia per ogni capacità,
quindi si può avere ad esempio testo+embedding con llama.cpp e immagini con Ollama.

Per aggiungere un motore: crea `engines/<nome>.py` con una sottoclasse di `EngineStrategy`,
imposta `name` e registrala in `engines/__init__.py` (ENGINES).
"""

from __future__ import annotations

import json
import re
from abc import ABC
from pathlib import Path
from typing import Any

TEXT, VISION, EMBED = "text", "vision", "embed"


def parse_json(text: str) -> dict[str, Any]:
    """JSON dalla risposta del modello, tollerando testo prima/dopo."""
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, TypeError):
        m = re.search(r"\{.*\}", text or "", re.S)
        if m:
            try:
                data = json.loads(m.group(0))
                return data if isinstance(data, dict) else {}
            except json.JSONDecodeError:
                pass
    return {}


class EngineUnsupported(RuntimeError):
    """Il motore non offre questa capacità."""


class EngineStrategy(ABC):
    name: str = ""
    label: str = ""                 # nome leggibile ("llama.cpp", "Ollama"…)
    supports_text = False
    supports_vision = False
    supports_embeddings = False

    def __init__(self, model: str | None = None, vision_model: str | None = None,
                 embed_model: str | None = None, **options: Any) -> None:
        self.model = model or ""
        self.vision_model = vision_model or ""
        self.embed_model = embed_model or ""
        self.options = options

    # ---------------------------------------------------------------- stato
    def supports(self, capability: str) -> bool:
        return {TEXT: self.supports_text, VISION: self.supports_vision,
                EMBED: self.supports_embeddings}.get(capability, False)

    def model_for(self, capability: str) -> str:
        return {TEXT: self.model, VISION: self.vision_model, EMBED: self.embed_model}.get(capability, "")

    async def available(self, capability: str = TEXT) -> bool:
        """True se il motore può svolgere ora questa capacità (modello presente, servizio attivo…)."""
        return False

    def why_unavailable(self, capability: str = TEXT) -> str:
        """Suggerimento per l'utente quando `available` è False."""
        return f"{self.label or self.name}: {capability} non disponibile"

    def describe(self) -> dict[str, Any]:
        return {"engine": self.name, "label": self.label, "model": self.model,
                "vision_model": self.vision_model, "embed_model": self.embed_model}

    def close(self) -> None:
        """Libera le risorse (modelli in memoria)."""

    def prefers_phases(self) -> bool:
        """Per motori che servono sia testo sia immagini (es. il server): alternarli in indicizzazione?"""
        return False

    def release(self, capability: str) -> None:
        """Libera la memoria del modello usato per `capability` (verrà ricaricato al bisogno)."""

    # ---------------------------------------------------------------- testo
    async def generate_json(self, prompt: str, system: str = "", schema: dict[str, Any] | None = None,
                            model: str | None = None) -> dict[str, Any]:
        raise EngineUnsupported(f"{self.name}: generazione di testo non supportata")

    async def chat(self, messages: list[dict[str, str]], model: str | None = None) -> str:
        raise EngineUnsupported(f"{self.name}: chat non supportata")

    # ---------------------------------------------------------------- immagini
    async def describe_image(self, image: Path | bytes, prompt: str, as_json: bool = False) -> Any:
        raise EngineUnsupported(f"{self.name}: lettura immagini non supportata")

    # ---------------------------------------------------------------- vettori
    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise EngineUnsupported(f"{self.name}: embedding non supportati")
