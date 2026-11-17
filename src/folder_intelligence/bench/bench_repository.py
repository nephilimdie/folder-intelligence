"""Repository del banco di prova: il file `bench.json` nella cartella dei campioni.

`bench.json` lo scrive l'app con l'elenco dei file (`--bench-init`); tu aggiungi le risposte giuste:
    {"cases": [{"file": "scontrino.jpg", "kind": "receipt", "date": "2025-03-14",
                "amount": 18.5, "issuer": "Esselunga", "words": ["latte", "pane"]}, …]}
"""

from __future__ import annotations

import json
from pathlib import Path

from .bench_case import BenchCase

FILENAME = "bench.json"
SAMPLE_EXT = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp", ".tif", ".tiff", ".pdf"}
MAX_TEMPLATE_FILES = 40


class BenchRepository:
    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.path = folder / FILENAME

    def cases(self) -> list[BenchCase]:
        data = json.loads(self.path.read_text(encoding="utf-8"))
        return [BenchCase.from_dict(c) for c in data.get("cases", [])]

    def write_template(self) -> int:
        """Crea `bench.json` con i file della cartella e risposte vuote (non sovrascrive)."""
        if self.path.exists():
            raise FileExistsError(str(self.path))
        files = sorted(p for p in self.folder.rglob("*") if p.is_file() and p.suffix.lower() in SAMPLE_EXT)
        cases = [BenchCase(str(p.relative_to(self.folder))).to_dict() for p in files[:MAX_TEMPLATE_FILES]]
        self.path.write_text(json.dumps({"cases": cases}, ensure_ascii=False, indent=2), encoding="utf-8")
        return len(cases)
