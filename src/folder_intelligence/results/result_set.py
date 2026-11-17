"""I file trovati da una ricerca, con filtri e gruppi (oggetto di dominio)."""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from ..documents import DocumentFields
from ..i18n import _
from .constants import MAX_HITS
from .filters import Filters
from .hit import Hit
from .refiner import refine


@dataclass
class ResultSet:
    query: str
    root: str
    hits: list[Hit]
    filters: Filters = field(default_factory=Filters)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    created: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    @classmethod
    def from_search(cls, query: str, root: Path, hits: list, index) -> "ResultSet":
        out = []
        for h in hits[:MAX_HITS]:
            d = h.doc or index.get(h.path) or {}
            meta = d.get("metadata") or {}
            fields = DocumentFields.from_dict(meta.get("fields") or {"amount": meta.get("amount")})
            out.append(Hit(path=h.path, title=d.get("title") or Path(h.path).name, score=round(h.score, 4),
                           sources=list(dict.fromkeys(h.sources)), snippet=h.snippet,
                           description=d.get("description") or "", size=d.get("size") or 0,
                           mtime=d.get("mtime") or 0.0, date=fields.date or meta.get("date"), amount=fields.amount))
        return cls(query=query, root=str(root), hits=out)

    def visible(self) -> list[Hit]:
        return self.filters.apply(self.hits)

    def refine(self, text: str) -> list[str]:
        """Applica i filtri descritti a parole; restituisce cosa è cambiato ([] = niente di riconosciuto)."""
        return refine(self.filters, text)

    def groups(self) -> list[tuple[str, list[Hit]]]:
        """Prima i 3 più pertinenti, poi gli altri per cartella (o un solo gruppo se ordinati)."""
        hits = self.visible()
        if self.filters.sort != "relevance" or len(hits) <= 4:
            return [("", hits)]
        top, rest = hits[:3], hits[3:]
        by_folder: dict[str, list[Hit]] = {}
        for h in rest:
            by_folder.setdefault(str(Path(h.path).parent), []).append(h)
        groups = [(_("Più pertinenti"), top)]
        groups += sorted(((f, hs) for f, hs in by_folder.items()), key=lambda g: -len(g[1]))
        return groups

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "query": self.query, "root": self.root, "created": self.created,
                "filters": asdict(self.filters), "total": len(self.hits),
                "items": [{**asdict(h), "name": h.name, "folder": str(Path(h.path).parent)} for h in self.visible()]}
