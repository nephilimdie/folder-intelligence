"""Un file trovato da una ricerca (DTO)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass
class Hit:
    path: str
    title: str
    score: float
    sources: list[str] = field(default_factory=list)
    snippet: str = ""
    description: str = ""
    size: int = 0
    mtime: float = 0.0
    date: str | None = None          # data del documento (dall'AI o dal testo), se nota
    amount: float | None = None      # importo del documento (campi estratti), se noto

    @property
    def name(self) -> str:
        return Path(self.path).name

    @property
    def ext(self) -> str:
        return Path(self.path).suffix.lower()

    @property
    def when(self) -> datetime:
        if self.date:
            try:
                return datetime.fromisoformat(self.date[:10])
            except ValueError:
                pass
        return datetime.fromtimestamp(self.mtime or 0)
