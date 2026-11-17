"""Esito di una combinazione su un file (DTO)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class BenchResult:
    config: str
    file: str
    checks: dict[str, bool] = field(default_factory=dict)   # campo → giusto?
    seconds: float = 0.0
    engine: str = ""
    error: str = ""

    @property
    def correct(self) -> int:
        return sum(self.checks.values())

    @property
    def total(self) -> int:
        return len(self.checks)
