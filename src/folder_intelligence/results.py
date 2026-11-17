"""Risultati di ricerca come oggetto su cui lavorare: filtri, gruppi, Finder, ricerche salvate.

- `ResultSet`: i file trovati per una ricerca (fino a MAX_HITS), con filtri applicabili parlando
  ("solo i pdf", "del 2024", "togli Downloads", "ordina per data");
- `to_finder`: mette i risultati nel Finder come **etichetta** macOS (default: nessun file spostato)
  oppure come **cartella di alias** (collegamenti) — scelta nelle impostazioni;
- ricerche salvate e cronologia delle ricerche, cifrate in `searches.enc`.
"""

from __future__ import annotations

import json
import os
import plistlib
import re
import subprocess
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from . import crypto
from .audit import app_home
from .i18n import N_, _

MAX_HITS = 100
HISTORY = 15
PURPOSE = "searches"
TAG_XATTR = "com.apple.metadata:_kMDItemUserTags"

TYPE_GROUPS = {
    "pdf": {".pdf"},
    "images": {".jpg", ".jpeg", ".png", ".heic", ".heif", ".gif", ".webp", ".tif", ".tiff", ".bmp"},
    "documents": {".pdf", ".doc", ".docx", ".odt", ".pages", ".rtf", ".txt", ".md"},
    "sheets": {".xls", ".xlsx", ".numbers", ".ods", ".csv"},
    "slides": {".ppt", ".pptx", ".key", ".odp"},
    "video": {".mp4", ".mov", ".m4v", ".mkv", ".avi"},
    "audio": {".mp3", ".m4a", ".wav", ".aac", ".flac", ".opus"},
    "email": {".eml", ".emlx", ".msg"},
}
TYPE_WORDS = {
    "pdf": ("pdf",), "images": ("immagin", "foto", "image", "photo", "picture", "screenshot"),
    "documents": ("document", "testi", "word", "text"), "sheets": ("fogli", "excel", "spreadsheet", "tabell"),
    "slides": ("presentazion", "slide", "keynote", "powerpoint"), "video": ("video", "filmat"),
    "audio": ("audio", "musica", "registrazion", "music"), "email": ("email", "mail", "messagg"),
}


@dataclass
class Hit:
    path: str
    title: str
    score: float
    sources: list[str] = field(default_factory=list)
    snippet: str = ""
    description: str = ""
    size: int = 0
    mtime: float = 0.0
    date: str | None = None          # data del documento (dall'AI o dal testo), se nota

    @property
    def name(self) -> str:
        return Path(self.path).name

    @property
    def ext(self) -> str:
        return Path(self.path).suffix.lower()

    @property
    def when(self) -> datetime:
        if self.date:
            try:
                return datetime.fromisoformat(self.date[:10])
            except ValueError:
                pass
        return datetime.fromtimestamp(self.mtime or 0)


@dataclass
class Filters:
    types: list[str] = field(default_factory=list)       # chiavi di TYPE_GROUPS
    year: int | None = None
    month: int | None = None
    exclude: list[str] = field(default_factory=list)     # cartelle escluse (nomi o percorsi)
    only: list[str] = field(default_factory=list)        # solo queste cartelle
    sort: str = "relevance"                              # relevance | date | name | size

    def describe(self) -> str:
        parts = []
        if self.types:
            parts.append("/".join(self.types))
        if self.year:
            parts.append(f"{self.month:02d}/{self.year}" if self.month else str(self.year))
        if self.only:
            parts.append(_("solo in {f}", f=", ".join(self.only)))
        if self.exclude:
            parts.append(_("senza {f}", f=", ".join(self.exclude)))
        if self.sort != "relevance":
            parts.append(_("per {s}", s=_(SORT_LABELS[self.sort])))
        return " · ".join(parts)


SORT_LABELS = {"relevance": N_("pertinenza"), "date": N_("data"), "name": N_("nome"), "size": N_("dimensione")}


@dataclass
class ResultSet:
    query: str
    root: str
    hits: list[Hit]
    filters: Filters = field(default_factory=Filters)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    created: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    @classmethod
    def from_search(cls, query: str, root: Path, hits: list, index) -> "ResultSet":
        out = []
        for h in hits[:MAX_HITS]:
            d = h.doc or index.get(h.path) or {}
            meta = d.get("metadata") or {}
            out.append(Hit(path=h.path, title=d.get("title") or Path(h.path).name, score=round(h.score, 4),
                           sources=list(dict.fromkeys(h.sources)), snippet=h.snippet,
                           description=d.get("description") or "", size=d.get("size") or 0,
                           mtime=d.get("mtime") or 0.0, date=meta.get("date")))
        return cls(query=query, root=str(root), hits=out)

    # ------------------------------------------------------------ filtri
    def visible(self) -> list[Hit]:
        f = self.filters
        hits = list(self.hits)
        if f.types:
            exts = set().union(*(TYPE_GROUPS[t] for t in f.types if t in TYPE_GROUPS))
            hits = [h for h in hits if h.ext in exts]
        if f.year:
            hits = [h for h in hits if h.when.year == f.year and (not f.month or h.when.month == f.month)]
        if f.only:
            hits = [h for h in hits if any(_in_folder(h.path, x) for x in f.only)]
        if f.exclude:
            hits = [h for h in hits if not any(_in_folder(h.path, x) for x in f.exclude)]
        key = {"date": lambda h: -h.when.timestamp(), "name": lambda h: h.name.lower(),
               "size": lambda h: -h.size}.get(f.sort)
        return sorted(hits, key=key) if key else hits

    def refine(self, text: str) -> list[str]:
        """Applica i filtri descritti a parole; restituisce cosa è cambiato ([] = niente di riconosciuto)."""
        from .dates import query_dates
        from .intents import normalize

        t = normalize(text)
        changed = []
        if re.search(r"\b(tutti|tutto|togli i filtri|senza filtri|reset|all of them|clear filters?|everything)\b", t):
            self.filters = Filters()
            return ["reset"]
        types = [k for k, words in TYPE_WORDS.items() if any(w in t for w in words)]
        if types:
            self.filters.types = types
            changed.append("types")
        specs = query_dates(text)
        if specs:
            self.filters.year, self.filters.month = specs[0].start.year, specs[0].start.month \
                if specs[0].kind != "day" else specs[0].start.month
            changed.append("date")
        else:
            m = re.search(r"\b(?:del|nel|dal|from|in|of)\s+((?:19|20)\d{2})\b", t) or re.search(r"\b((?:19|20)\d{2})\b", t)
            if m:
                self.filters.year, self.filters.month = int(m.group(1)), None
                changed.append("date")
        m = re.search(r"\b(?:togli|escludi|senza|tranne|remove|exclude|without|except)\s+(?:quelli\s+|those\s+)?"
                      r"(?:in|da|dalla cartella|from|in the)?\s*([\w~/.\- ]+?)\s*$", t)
        if m:
            self.filters.exclude.append(m.group(1).strip())
            changed.append("exclude")
        m = re.search(r"\b(?:solo|soltanto|only)\s+(?:quelli\s+)?(?:in|nella cartella|from|in the)\s+([\w~/.\- ]+?)\s*$", t)
        if m and "exclude" not in changed:
            self.filters.only = [m.group(1).strip()]
            changed.append("only")
        m = re.search(r"\b(?:ordina|ordinali|sort)\b.*\b(data|date|recent|nome|name|alfabet|dimension|size|grand|"
                      r"pertinen|relevan)", t)
        if m:
            w = m.group(1)
            self.filters.sort = ("date" if w in ("data", "date", "recent") else "name" if w in ("nome", "name", "alfabet")
                                 else "size" if w in ("dimension", "size", "grand") else "relevance")
            changed.append("sort")
        return changed

    # ------------------------------------------------------------ gruppi
    def groups(self) -> list[tuple[str, list[Hit]]]:
        """Prima i 3 più pertinenti, poi gli altri per cartella (o un solo gruppo se ordinati)."""
        hits = self.visible()
        if self.filters.sort != "relevance" or len(hits) <= 4:
            return [("", hits)]
        top, rest = hits[:3], hits[3:]
        by_folder: dict[str, list[Hit]] = {}
        for h in rest:
            by_folder.setdefault(str(Path(h.path).parent), []).append(h)
        groups = [(_("Più pertinenti"), top)]
        groups += sorted(((f, hs) for f, hs in by_folder.items()), key=lambda g: -len(g[1]))
        return groups

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "query": self.query, "root": self.root, "created": self.created,
                "filters": asdict(self.filters), "total": len(self.hits),
                "items": [{**asdict(h), "name": h.name, "folder": str(Path(h.path).parent)} for h in self.visible()]}


def _in_folder(path: str, folder: str) -> bool:
    folder = folder.strip().strip("/").lower()
    parts = [p.lower() for p in Path(path).parent.parts]
    if "/" in folder or folder.startswith("~"):
        return str(Path(path).parent).lower().startswith(str(Path(folder).expanduser()).lower())
    return any(p == folder or p.startswith(folder) for p in parts)


# ===================================================================== Finder
def read_tags(path: str | Path) -> list[str]:
    r = subprocess.run(["xattr", "-px", TAG_XATTR, str(path)], capture_output=True, text=True)
    if r.returncode != 0:
        return []
    try:
        raw = bytes.fromhex(r.stdout.replace("\n", "").replace(" ", ""))
        return [str(t) for t in plistlib.loads(raw)]
    except Exception:
        return []


def write_tags(path: str | Path, tags: list[str]) -> None:
    data = plistlib.dumps(tags, fmt=plistlib.FMT_BINARY).hex()
    r = subprocess.run(["xattr", "-wx", TAG_XATTR, data, str(path)], capture_output=True, text=True)
    if r.returncode != 0:
        raise OSError(r.stderr.strip() or "xattr failed")


def add_tag(path: str | Path, tag: str) -> bool:
    """Aggiunge l'etichetta del Finder (il file non viene spostato né modificato). True se nuova."""
    current = read_tags(path)
    if any(t.split("\n")[0] == tag for t in current):
        return False
    write_tags(path, [*current, f"{tag}\n0"])
    return True


def remove_tag(path: str | Path, tag: str) -> bool:
    current = read_tags(path)
    kept = [t for t in current if t.split("\n")[0] != tag]
    if kept == current:
        return False
    write_tags(path, kept)
    return True


def safe_name(name: str) -> str:
    name = re.sub(r"[/:\\\x00-\x1f]+", " ", name).strip(" .")
    return (name or "Risultati")[:60]


def aliases_root() -> Path:
    from . import settings

    return Path(settings.load().aliases_folder).expanduser()


def to_finder(paths: list[str], name: str, mode: str | None = None, open_finder: bool = True) -> dict[str, Any]:
    """Mette i file nel Finder: `tag` (etichetta) o `aliases` (cartella di collegamenti)."""
    from . import settings

    mode = mode or settings.load().results_destination
    name = safe_name(name)
    done, failed = 0, []
    if mode == "aliases":
        folder = aliases_root() / name
        folder.mkdir(parents=True, exist_ok=True)
        for p in paths:
            link = folder / Path(p).name
            n = 2
            while link.exists() or link.is_symlink():
                if link.is_symlink() and os.readlink(link) == p:
                    break
                link = folder / f"{Path(p).stem} ({n}){Path(p).suffix}"
                n += 1
            else:
                try:
                    link.symlink_to(p)
                    done += 1
                except OSError as e:
                    failed.append(f"{Path(p).name}: {e}")
        if open_finder:
            subprocess.run(["open", str(folder)], capture_output=True)
        return {"mode": "aliases", "name": name, "folder": str(folder), "added": done, "failed": failed}
    for p in paths:
        try:
            done += add_tag(p, name)
        except OSError as e:
            failed.append(f"{Path(p).name}: {e}")
    return {"mode": "tag", "name": name, "added": done, "tagged": len(paths) - len(failed), "failed": failed}


def remove_from_finder(paths: list[str], name: str) -> int:
    return sum(1 for p in paths if Path(p).exists() and remove_tag(p, safe_name(name)))


# =========================================================== salvate e cronologia
_lock = threading.RLock()


@dataclass
class SavedSearch:
    name: str
    query: str
    root: str
    filters: dict[str, Any] = field(default_factory=dict)
    destination: str | None = None      # "tag"/"aliases" se i risultati sono stati messi nel Finder
    auto: bool = True                   # il servizio in background aggiunge i nuovi file trovati
    paths: list[str] = field(default_factory=list)   # file già messi nel Finder
    created: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    updated: str | None = None


def _path() -> Path:
    return app_home() / "searches.enc"


def _load() -> dict[str, Any]:
    try:
        return json.loads(crypto.read_encrypted(_path(), PURPOSE))
    except Exception:
        return {"saved": [], "history": []}


def _store(data: dict[str, Any]) -> None:
    crypto.write_encrypted(_path(), json.dumps(data, ensure_ascii=False).encode(), PURPOSE)


def remember_query(query: str, root: str, count: int) -> None:
    with _lock:
        data = _load()
        hist = [h for h in data.get("history", []) if not (h["query"] == query and h["root"] == root)]
        hist.insert(0, {"query": query, "root": root, "count": count,
                        "when": datetime.now().isoformat(timespec="seconds")})
        data["history"] = hist[:HISTORY]
        _store(data)


def history() -> list[dict[str, Any]]:
    return _load().get("history", [])


def saved() -> list[SavedSearch]:
    return [SavedSearch(**s) for s in _load().get("saved", [])]


def get_saved(name: str) -> SavedSearch | None:
    n = name.strip().lower()
    return next((s for s in saved() if s.name.lower() == n), None) or \
        next((s for s in saved() if n and n in s.name.lower()), None)


def save_search(rs: ResultSet, name: str, destination: str | None = None, paths: list[str] | None = None) -> SavedSearch:
    with _lock:
        data = _load()
        items = [s for s in data.get("saved", []) if s["name"].lower() != name.lower()]
        old = next((s for s in data.get("saved", []) if s["name"].lower() == name.lower()), None)
        entry = SavedSearch(name=name, query=rs.query, root=rs.root, filters=asdict(rs.filters),
                            destination=destination or (old or {}).get("destination"),
                            paths=sorted(set((old or {}).get("paths", [])) | set(paths or [])),
                            updated=datetime.now().isoformat(timespec="seconds"))
        items.append(asdict(entry))
        data["saved"] = items
        _store(data)
        return entry


def update_saved(entry: SavedSearch) -> None:
    with _lock:
        data = _load()
        data["saved"] = [asdict(entry) if s["name"] == entry.name else s for s in data.get("saved", [])]
        _store(data)


def delete_saved(name: str) -> SavedSearch | None:
    with _lock:
        entry = get_saved(name)
        if entry is None:
            return None
        data = _load()
        data["saved"] = [s for s in data.get("saved", []) if s["name"] != entry.name]
        _store(data)
        return entry


async def run_saved(entry: SavedSearch, intelligence) -> ResultSet:
    hits = await intelligence.search(entry.query, Path(entry.root), MAX_HITS)
    rs = ResultSet.from_search(entry.query, Path(entry.root), hits, intelligence.index)
    rs.filters = Filters(**entry.filters) if entry.filters else Filters()
    return rs


async def refresh_saved(intelligence) -> int:
    """Per il servizio in background: i file nuovi che rispondono a una ricerca salvata
    finiscono anche loro nel Finder (etichetta o cartella di alias)."""
    added = 0
    for entry in saved():
        if not entry.auto or not entry.destination or not Path(entry.root).is_dir():
            continue
        rs = await run_saved(entry, intelligence)
        new = [h.path for h in rs.visible() if h.path not in entry.paths]
        if not new:
            continue
        res = to_finder(new, entry.name, entry.destination, open_finder=False)
        entry.paths = sorted(set(entry.paths) | set(new))
        entry.updated = datetime.now().isoformat(timespec="seconds")
        update_saved(entry)
        added += res.get("added", 0)
    return added
