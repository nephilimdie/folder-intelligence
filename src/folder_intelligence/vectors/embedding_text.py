"""Il testo da cui si calcola il vettore di un documento (uguale in indicizzazione e ricalcolo)."""

from __future__ import annotations

from typing import Any

TEXT_CHARS = 2800


def embedding_text(doc: dict[str, Any]) -> str:
    tags = doc.get("tags") or []
    return " ".join(str(part) for part in (
        doc.get("name", ""), doc.get("title", ""), doc.get("description", ""),
        " ".join(tags) if isinstance(tags, list) else tags, (doc.get("text") or "")[:TEXT_CHARS],
    ) if part)
