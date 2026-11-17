"""Comandi da terminale del banco: `--bench-init CARTELLA` e `--bench CARTELLA`."""

from __future__ import annotations

import asyncio
from pathlib import Path

from ..i18n import _
from .bench_report import report
from .bench_repository import BenchRepository
from .bench_runner import BenchRunner
from .default_configs import default_configs


def bench_init(folder: str) -> int:
    repo = BenchRepository(Path(folder).expanduser())
    try:
        n = repo.write_template()
    except FileExistsError:
        print(_("Esiste già {path}: aggiungi lì le risposte giuste.", path=repo.path))
        return 1
    print(_("Creato {path} con {n} file. Scrivi per ognuno le risposte giuste (kind, date, amount, issuer, "
            "words), poi lancia: folder-intelligence --bench {folder}", path=repo.path, n=n, folder=folder))
    return 0


def bench_run(folder: str, only: str | None = None) -> int:
    root = Path(folder).expanduser()
    repo = BenchRepository(root)
    if not repo.path.exists():
        print(_("Manca {path}: crealo con --bench-init {folder}", path=repo.path, folder=folder))
        return 1
    cases = [c for c in repo.cases() if c.labelled]
    if not cases:
        print(_("Nessun file con le risposte giuste in {path}.", path=repo.path))
        return 1
    wanted = {n.strip() for n in only.split(",")} if only else None
    configs = [c for c in default_configs() if wanted is None or c.name in wanted]
    runner = BenchRunner(root, on_progress=lambda msg: print(f"  … {msg}", flush=True))
    results, skipped = asyncio.run(runner.run(cases, configs))
    print()
    print(report(results, skipped))
    return 0
