"""Filtri applicati a una ricerca (DTO) e loro applicazione ai file trovati."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..i18n import _
from .constants import SORT_LABELS, TYPE_GROUPS
from .hit import Hit


def in_folder(path: str, folder: str) -> bool:
    folder = folder.strip().strip("/").lower()
    parts = [p.lower() for p in Path(path).parent.parts]
    if "/" in folder or folder.startswith("~"):
        return str(Path(path).parent).lower().startswith(str(Path(folder).expanduser()).lower())
    return any(p == folder or p.startswith(folder) for p in parts)


def _money(value: float) -> str:
    return f"{value:,.2f}".replace(",", "x").replace(".", ",").replace("x", ".").removesuffix(",00") + " €"


@dataclass
class Filters:
    types: list[str] = field(default_factory=list)       # chiavi di TYPE_GROUPS
    year: int | None = None
    month: int | None = None
    exclude: list[str] = field(default_factory=list)     # cartelle escluse (nomi o percorsi)
    only: list[str] = field(default_factory=list)        # solo queste cartelle
    sort: str = "relevance"                              # relevance | date | name | size
    min_amount: float | None = None                      # importo del documento (campi estratti)
    max_amount: float | None = None

    def apply(self, hits: list[Hit]) -> list[Hit]:
        if self.types:
            exts = set().union(*(TYPE_GROUPS[t] for t in self.types if t in TYPE_GROUPS))
            hits = [h for h in hits if h.ext in exts]
        if self.year:
            hits = [h for h in hits if h.when.year == self.year and (not self.month or h.when.month == self.month)]
        if self.only:
            hits = [h for h in hits if any(in_folder(h.path, x) for x in self.only)]
        if self.exclude:
            hits = [h for h in hits if not any(in_folder(h.path, x) for x in self.exclude)]
        if self.min_amount is not None:
            hits = [h for h in hits if h.amount is not None and h.amount >= self.min_amount]
        if self.max_amount is not None:
            hits = [h for h in hits if h.amount is not None and h.amount <= self.max_amount]
        key = {"date": lambda h: -h.when.timestamp(), "name": lambda h: h.name.lower(),
               "size": lambda h: -h.size}.get(self.sort)
        return sorted(hits, key=key) if key else list(hits)

    def describe(self) -> str:
        parts = []
        if self.types:
            parts.append("/".join(self.types))
        if self.year:
            parts.append(f"{self.month:02d}/{self.year}" if self.month else str(self.year))
        if self.only:
            parts.append(_("solo in {f}", f=", ".join(self.only)))
        if self.exclude:
            parts.append(_("senza {f}", f=", ".join(self.exclude)))
        if self.min_amount is not None and self.max_amount is not None:
            parts.append(f"{_money(self.min_amount)}–{_money(self.max_amount)}")
        elif self.min_amount is not None:
            parts.append("≥ " + _money(self.min_amount))
        elif self.max_amount is not None:
            parts.append("≤ " + _money(self.max_amount))
        if self.sort != "relevance":
            parts.append(_("per {s}", s=_(SORT_LABELS[self.sort])))
        return " · ".join(parts)
