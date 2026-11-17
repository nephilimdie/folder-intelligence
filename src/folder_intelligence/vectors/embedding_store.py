"""Repository dei vettori: con quale modello sono stati calcolati e quali mancano.

Vettori di modelli diversi non si confrontano (dimensioni e scale diverse): quando cambi il modello
di ricerca, i vecchi vettori vengono tolti e ricalcolati un po' alla volta (`Reembedder`).
Nel frattempo la ricerca per parole funziona come sempre.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from ..index import pack

if TYPE_CHECKING:
    from ..index import Index

_SCHEMA = "CREATE TABLE IF NOT EXISTS index_settings(key TEXT PRIMARY KEY, value TEXT)"
_KEY = "embed_model"


class EmbeddingStore:
    def __init__(self, index: "Index") -> None:
        self.index = index
        with self.index.conn() as c:
            c.execute(_SCHEMA)

    def model(self) -> str | None:
        with self.index.conn() as c:
            row = c.execute("SELECT value FROM index_settings WHERE key = ?", (_KEY,)).fetchone()
        return row["value"] if row else None

    def use_model(self, model: str) -> bool:
        """Registra il modello in uso. True se era diverso: i vecchi vettori sono stati tolti."""
        current = self.model()
        if not model or current == model:
            return False
        with self.index.conn() as c:
            if current is not None:
                c.execute("UPDATE docs SET embedding = NULL")
            c.execute("INSERT INTO index_settings(key, value) VALUES (?, ?) "
                      "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (_KEY, model))
        return current is not None

    def missing(self, limit: int) -> list[dict[str, Any]]:
        """Documenti con contenuto ma senza vettore (i più recenti prima)."""
        with self.index.conn() as c:
            rows = c.execute("SELECT path, name, title, description, tags, text FROM docs "
                             "WHERE embedding IS NULL AND (text != '' OR description != '') "
                             "ORDER BY mtime DESC LIMIT ?", (limit,)).fetchall()
        docs = []
        for r in rows:
            doc = dict(r)
            try:
                doc["tags"] = json.loads(doc["tags"] or "[]")
            except json.JSONDecodeError:
                doc["tags"] = []
            docs.append(doc)
        return docs

    def count_missing(self) -> int:
        with self.index.conn() as c:
            row = c.execute("SELECT COUNT(*) n FROM docs WHERE embedding IS NULL "
                            "AND (text != '' OR description != '')").fetchone()
        return int(row["n"] or 0)

    def save(self, path: str, vector: list[float]) -> None:
        with self.index.conn() as c:
            c.execute("UPDATE docs SET embedding = ? WHERE path = ?", (pack(vector), path))
