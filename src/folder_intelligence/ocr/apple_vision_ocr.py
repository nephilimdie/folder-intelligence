"""OCR con Apple Vision (lo stesso motore di "Testo attivo" in Foto): locale, veloce, multilingue."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from ..threads import run_blocking
from .ocr_engine_kind import OcrEngineKind
from .ocr_result import OcrResult
from .ocr_strategy import OcrStrategy

LANGUAGES = ["it-IT", "en-US", "fr-FR", "de-DE", "es-ES"]


def _recognize(path: str) -> tuple[str, float | None]:
    import objc
    import Vision
    from Foundation import NSURL

    with objc.autorelease_pool():
        handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(NSURL.fileURLWithPath_(path), None)
        request = Vision.VNRecognizeTextRequest.alloc().init()
        request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
        request.setUsesLanguageCorrection_(True)
        request.setRecognitionLanguages_(LANGUAGES)
        ok, _error = handler.performRequests_error_([request], None)
        if not ok:
            return "", None
        lines, confidences = [], []
        for observation in request.results() or []:
            candidates = observation.topCandidates_(1)
            if candidates:
                lines.append(str(candidates[0].string()))
                confidences.append(float(candidates[0].confidence()))
    return "\n".join(lines), (sum(confidences) / len(confidences) if confidences else None)


class AppleVisionOcr(OcrStrategy):
    kind = OcrEngineKind.APPLE

    async def available(self) -> bool:
        return sys.platform == "darwin" and importlib.util.find_spec("Vision") is not None

    async def read(self, image: Path) -> OcrResult:
        try:
            text, confidence = await run_blocking(_recognize, str(image))
        except Exception:
            return OcrResult.empty(self.kind)
        return OcrResult(text.strip(), self.kind, confidence)
