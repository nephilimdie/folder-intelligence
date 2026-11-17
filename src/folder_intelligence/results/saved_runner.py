"""Riesegue le ricerche salvate; il servizio in background mette nel Finder i file nuovi."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from .constants import MAX_HITS
from .filters import Filters
from .finder import to_finder
from .result_set import ResultSet
from .saved_search import SavedSearch
from .search_repository import SearchRepository


async def run_saved(entry: SavedSearch, intelligence) -> ResultSet:
    hits = await intelligence.search(entry.query, Path(entry.root), MAX_HITS)
    rs = ResultSet.from_search(entry.query, Path(entry.root), hits, intelligence.index)
    known = set(asdict(Filters()))
    rs.filters = Filters(**{k: v for k, v in (entry.filters or {}).items() if k in known})
    return rs


async def refresh_saved(intelligence, repository: SearchRepository) -> int:
    """I file nuovi che rispondono a una ricerca salvata finiscono anche loro nel Finder."""
    added = 0
    for entry in repository.saved():
        if not entry.auto or not entry.destination or not Path(entry.root).is_dir():
            continue
        rs = await run_saved(entry, intelligence)
        new = [h.path for h in rs.visible() if h.path not in entry.paths]
        if not new:
            continue
        res = to_finder(new, entry.name, entry.destination, open_finder=False)
        entry.paths = sorted(set(entry.paths) | set(new))
        entry.updated = datetime.now().isoformat(timespec="seconds")
        repository.update(entry)
        added += res.get("added", 0)
    return added
