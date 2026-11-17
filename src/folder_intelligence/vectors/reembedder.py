"""Ricalcola i vettori mancanti (dopo un cambio di modello di ricerca, o se mancavano)."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from .embedding_store import EmbeddingStore
from .embedding_text import embedding_text

if TYPE_CHECKING:
    from ..ai import AIEngine

BATCH = 16


class Reembedder:
    def __init__(self, store: EmbeddingStore, ai: "AIEngine | None") -> None:
        self.store = store
        self.ai = ai

    async def run(self, limit: int = 64, cancel: threading.Event | None = None) -> int:
        """Un blocco di lavoro: al massimo `limit` documenti. Restituisce quanti ne ha completati."""
        if not (self.ai and await self.ai.can_embed()):
            return 0
        docs = self.store.missing(limit)
        done = 0
        for start in range(0, len(docs), BATCH):
            if cancel is not None and cancel.is_set():
                break
            batch = docs[start:start + BATCH]
            vectors = await self.ai.embed([embedding_text(d) for d in batch])
            for doc, vector in zip(batch, vectors):
                self.store.save(doc["path"], vector)
                done += 1
        return done
