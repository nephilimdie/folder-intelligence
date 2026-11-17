"""Estrattori di contenuto: un file = un tipo di contenuto.

Per aggiungerne uno crea qui (o in ~/.folder_intelligence/extractors/) un file con una
sottoclasse di `Extractor` (vedi base.py). Viene caricato automaticamente.
"""

from __future__ import annotations

from .base import ExtractContext, Extraction, Extractor


def load_extractors() -> list[Extractor]:
    from ..plugins import load

    return sorted(load(__name__, Extractor, user_dir="extractors"), key=lambda e: e.priority)


__all__ = ["Extractor", "Extraction", "ExtractContext", "load_extractors"]
