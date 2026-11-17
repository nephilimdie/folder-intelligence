"""Esegue il banco: ogni combinazione legge ogni file con un indice temporaneo (quello vero non si tocca)."""

from __future__ import annotations

import tempfile
import time
from pathlib import Path
from typing import Callable

from .bench_case import BenchCase
from .bench_config import BenchConfig
from .bench_result import BenchResult
from .field_scorer import score


class BenchRunner:
    def __init__(self, folder: Path, on_progress: Callable[[str], None] = lambda _msg: None) -> None:
        self.folder = folder
        self.on_progress = on_progress

    async def run(self, cases: list[BenchCase], configs: list[BenchConfig]) -> tuple[list[BenchResult], list[str]]:
        """(risultati, combinazioni saltate con il motivo)."""
        from ..ai import create_ai

        results: list[BenchResult] = []
        skipped: list[str] = []
        for config in configs:
            ai = create_ai(vision_model=config.vision_model)
            try:
                missing = [m for m in config.models() if not await ai.has_model(m)]
                if missing:
                    skipped.append(f"{config.name}: {', '.join(missing)}")
                    continue
                results += await self._run_config(ai, config, cases)
            finally:
                ai.close()
        return results, skipped

    async def _run_config(self, ai, config: BenchConfig, cases: list[BenchCase]) -> list[BenchResult]:
        from ..index import Index
        from ..intelligence import DocumentIntelligence
        from ..ocr import build_ocr

        out = []
        with tempfile.TemporaryDirectory(prefix="fi-bench-") as tmp:
            di = DocumentIntelligence(ai, index=Index(Path(tmp) / "bench.enc"), use_vision=True)
            di.configure_reading(build_ocr(config.ocr, ai, config.ocr_model), config.document_model,
                                 config.summary_model)
            for n, case in enumerate(cases, 1):
                self.on_progress(f"{config.name} {n}/{len(cases)} · {case.file}")
                out.append(await self._run_case(di, config, case))
        return out

    async def _run_case(self, di, config: BenchConfig, case: BenchCase) -> BenchResult:
        path = self.folder / case.file
        start = time.monotonic()
        try:
            doc = await di.analyze(path, force=True, deep=True)
        except Exception as e:  # un file che fallisce non ferma il banco
            return BenchResult(config.name, case.file, error=str(e)[:200])
        return BenchResult(config.name, case.file, score(case, doc), round(time.monotonic() - start, 2),
                           str(doc.get("engine") or ""))
