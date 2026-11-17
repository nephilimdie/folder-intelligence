"""Come usare un modello di embedding (DTO): prefisso delle richieste e soglie di somiglianza.

Le soglie dipendono dal modello: bge-m3 e qwen3-embedding danno punteggi su scale diverse.
Quelle di qwen3-embedding sono un punto di partenza da confermare col banco di prova.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class EmbeddingProfile:
    family: str
    query_prefix: str = ""      # alcuni modelli vogliono un'istruzione davanti alla richiesta (non ai documenti)
    min_score: float = 0.53     # "cerca": risultati precisi
    loose_min: float = 0.40     # domande: qualche fonte in più
    margin: float = 0.07        # distanza massima dal risultato migliore

    def query(self, text: str) -> str:
        return self.query_prefix + text

    @staticmethod
    def for_model(model: str | None) -> "EmbeddingProfile":
        name = (model or "").lower()
        profile = next((p for family, p in PROFILES.items() if name.startswith(family)), PROFILES["bge-m3"])
        override = os.environ.get("FI_SEMANTIC_MIN")
        if override:
            from dataclasses import replace

            profile = replace(profile, min_score=float(override))
        return profile


PROFILES = {
    "bge-m3": EmbeddingProfile("bge-m3"),
    "qwen3-embedding": EmbeddingProfile(
        "qwen3-embedding",
        query_prefix="Instruct: Given a request to find personal files, retrieve the documents that match it\nQuery: ",
        min_score=0.50, loose_min=0.38, margin=0.08,
    ),
}
