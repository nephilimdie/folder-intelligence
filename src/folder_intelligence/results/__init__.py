"""Risultati di ricerca come oggetto su cui lavorare: filtri, gruppi, Finder, ricerche salvate.

- `ResultSet`: i file trovati per una ricerca (fino a MAX_HITS), con filtri applicabili parlando
  ("solo i pdf", "del 2024", "togli Downloads", "sopra 100 €", "ordina per data");
- `to_finder`: mette i risultati nel Finder come etichetta macOS (default) o cartella di alias;
- ricerche salvate e cronologia (`SearchRepository`, cifrate in `searches.enc`).

Le funzioni qui sotto sono l'interfaccia usata dal resto dell'app e delegano al repository.
"""

from __future__ import annotations

import subprocess  # i test lo sostituiscono per non aprire il Finder
from dataclasses import asdict

from .constants import HISTORY, MAX_HITS, SORT_LABELS, TYPE_GROUPS, TYPE_WORDS
from .filters import Filters
from .finder import aliases_root, remove_from_finder, safe_name, to_finder
from .finder_tags import add_tag, read_tags, remove_tag, write_tags
from .hit import Hit
from .result_set import ResultSet
from .saved_runner import refresh_saved as _refresh_saved
from .saved_runner import run_saved
from .saved_search import SavedSearch
from .search_repository import SearchRepository

repository = SearchRepository()


def remember_query(query: str, root: str, count: int) -> None:
    repository.remember_query(query, root, count)


def history() -> list[dict]:
    return repository.history()


def saved() -> list[SavedSearch]:
    return repository.saved()


def get_saved(name: str) -> SavedSearch | None:
    return repository.get(name)


def save_search(rs: ResultSet, name: str, destination: str | None = None,
                paths: list[str] | None = None) -> SavedSearch:
    return repository.save(SavedSearch(name=name, query=rs.query, root=rs.root, filters=asdict(rs.filters),
                                       destination=destination, paths=sorted(set(paths or []))))


def update_saved(entry: SavedSearch) -> None:
    repository.update(entry)


def delete_saved(name: str) -> SavedSearch | None:
    return repository.delete(name)


async def refresh_saved(intelligence) -> int:
    return await _refresh_saved(intelligence, repository)


__all__ = ["subprocess", "Filters", "HISTORY", "Hit", "MAX_HITS", "ResultSet", "SORT_LABELS", "SavedSearch", "SearchRepository",
           "TYPE_GROUPS", "TYPE_WORDS", "add_tag", "aliases_root", "delete_saved", "get_saved", "history",
           "read_tags", "refresh_saved", "remember_query", "remove_from_finder", "remove_tag", "run_saved",
           "safe_name", "save_search", "saved", "to_finder", "update_saved", "write_tags"]
