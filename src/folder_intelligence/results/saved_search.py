"""Una ricerca salvata con un nome (DTO)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class SavedSearch:
    name: str
    query: str
    root: str
    filters: dict[str, Any] = field(default_factory=dict)
    destination: str | None = None      # "tag"/"aliases" se i risultati sono stati messi nel Finder
    auto: bool = True                   # il servizio in background aggiunge i nuovi file trovati
    paths: list[str] = field(default_factory=list)   # file già messi nel Finder
    created: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    updated: str | None = None
