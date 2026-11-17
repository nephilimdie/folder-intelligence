"""Le combinazioni confrontate di default: quella attuale e le proposte di miglioramento."""

from __future__ import annotations

from ..ocr import OcrEngineKind
from .bench_config import BenchConfig


def default_configs() -> list[BenchConfig]:
    return [
        BenchConfig("prima", OcrEngineKind.TESSERACT),
        BenchConfig("apple-ocr", OcrEngineKind.APPLE),
        BenchConfig("apple+qwen3.5-riassunti", OcrEngineKind.APPLE, summary_model="qwen3.5:9b"),
        BenchConfig("apple+qwen3-vl-documenti", OcrEngineKind.APPLE, document_model="qwen3-vl:8b"),
        BenchConfig("glm-ocr", OcrEngineKind.MODEL, ocr_model="glm-ocr"),
    ]
