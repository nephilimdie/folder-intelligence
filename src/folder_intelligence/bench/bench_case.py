"""Un file del banco di prova con le risposte giuste scritte da te (DTO)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class BenchCase:
    file: str                                   # percorso relativo alla cartella del banco
    kind: str | None = None                     # tipo di documento atteso (receipt, invoice, …)
    date: str | None = None                     # YYYY-MM-DD
    amount: float | None = None
    issuer: str | None = None
    words: tuple[str, ...] = field(default_factory=tuple)   # parole che devono essere state lette

    @property
    def labelled(self) -> bool:
        return any((self.kind, self.date, self.amount is not None, self.issuer, self.words))

    def to_dict(self) -> dict[str, Any]:
        return {"file": self.file, "kind": self.kind, "date": self.date, "amount": self.amount,
                "issuer": self.issuer, "words": list(self.words)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BenchCase":
        amount = data.get("amount")
        return cls(file=str(data["file"]), kind=data.get("kind") or None, date=data.get("date") or None,
                   amount=float(amount) if isinstance(amount, (int, float)) else None,
                   issuer=data.get("issuer") or None, words=tuple(str(w) for w in data.get("words") or ()))
