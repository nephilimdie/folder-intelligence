"""Repository delle ricerche salvate e della cronologia (cifrate in `searches.enc`)."""

from __future__ import annotations

import json
import threading
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from .. import crypto
from .constants import HISTORY
from .saved_search import SavedSearch

PURPOSE = "searches"


class SearchRepository:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self._lock = threading.RLock()

    @property
    def path(self) -> Path:
        from ..audit import app_home

        return self._path or app_home() / "searches.enc"

    def _load(self) -> dict[str, Any]:
        try:
            return json.loads(crypto.read_encrypted(self.path, PURPOSE))
        except Exception:
            return {"saved": [], "history": []}

    def _store(self, data: dict[str, Any]) -> None:
        crypto.write_encrypted(self.path, json.dumps(data, ensure_ascii=False).encode(), PURPOSE)

    # ------------------------------------------------------------ cronologia
    def remember_query(self, query: str, root: str, count: int) -> None:
        with self._lock:
            data = self._load()
            hist = [h for h in data.get("history", []) if not (h["query"] == query and h["root"] == root)]
            hist.insert(0, {"query": query, "root": root, "count": count,
                            "when": datetime.now().isoformat(timespec="seconds")})
            data["history"] = hist[:HISTORY]
            self._store(data)

    def history(self) -> list[dict[str, Any]]:
        return self._load().get("history", [])

    # ------------------------------------------------------------ salvate
    def saved(self) -> list[SavedSearch]:
        return [SavedSearch(**s) for s in self._load().get("saved", [])]

    def get(self, name: str) -> SavedSearch | None:
        n = name.strip().lower()
        items = self.saved()
        return next((s for s in items if s.name.lower() == n), None) or \
            next((s for s in items if n and n in s.name.lower()), None)

    def save(self, entry: SavedSearch) -> SavedSearch:
        """Salva (o sostituisce per nome), conservando destinazione e file già messi nel Finder."""
        with self._lock:
            data = self._load()
            old = next((s for s in data.get("saved", []) if s["name"].lower() == entry.name.lower()), None)
            if old:
                entry.destination = entry.destination or old.get("destination")
                entry.paths = sorted(set(old.get("paths", [])) | set(entry.paths))
            entry.updated = datetime.now().isoformat(timespec="seconds")
            data["saved"] = [s for s in data.get("saved", []) if s["name"].lower() != entry.name.lower()]
            data["saved"].append(asdict(entry))
            self._store(data)
            return entry

    def update(self, entry: SavedSearch) -> None:
        with self._lock:
            data = self._load()
            data["saved"] = [asdict(entry) if s["name"] == entry.name else s for s in data.get("saved", [])]
            self._store(data)

    def delete(self, name: str) -> SavedSearch | None:
        with self._lock:
            entry = self.get(name)
            if entry is None:
                return None
            data = self._load()
            data["saved"] = [s for s in data.get("saved", []) if s["name"] != entry.name]
            self._store(data)
            return entry
