"""Motore MLX (Apple Silicon) — predisposto, non ancora attivo.

Il posto per `mlx-lm` (testo) e `mlx-vlm` (immagini, es. Qwen2.5-VL). Finché non è
implementato dichiara di non essere disponibile, così la facciata AIEngine ripiega su
un altro motore e l'utente riceve un messaggio chiaro.

Per completarlo: implementare `generate_json`/`chat` con `mlx_lm.load` + `mlx_lm.generate`
(eventualmente con un logits processor per lo schema JSON) e `describe_image` con
`mlx_vlm.load` + `mlx_vlm.generate`; poi impostare `supports_*` a True.
"""

from __future__ import annotations

from ..i18n import _

from .base import TEXT, EngineStrategy


class MlxEngine(EngineStrategy):
    name = "mlx"
    label = "MLX"

    async def available(self, capability: str = TEXT) -> bool:
        return False

    def why_unavailable(self, capability: str = TEXT) -> str:
        return _("il motore MLX non è ancora implementato: usa --engine llamacpp oppure ollama")
