"""Indice locale dei contenuti (SQLite + FTS5 + embedding).

Una riga per file analizzato: testo estratto, descrizione AI, tag, metadati, embedding.
- ricerca testuale: FTS5 con tokenizer che ignora gli accenti (bm25);
- ricerca semantica: similarità coseno sugli embedding (bge-m3, multilingue);
- incrementale: un file viene rianalizzato solo se dimensione o data cambiano.
"""

from __future__ import annotations

import atexit
import json
import math
import re
import sqlite3
import threading
import time
import weakref
from array import array
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from . import crypto
from .audit import app_home

PURPOSE = "index"
FLUSH_EVERY_CHANGES = 50
FLUSH_EVERY_SECONDS = 30
_OPEN: "weakref.WeakSet[Index]" = weakref.WeakSet()


def flush_all() -> None:
    """Salva su disco (cifrati) tutti gli indici aperti. Chiamato all'uscita."""
    for ix in list(_OPEN):
        try:
            ix.flush(force=True)
        except Exception:
            pass


atexit.register(flush_all)

SCHEMA = """
CREATE TABLE IF NOT EXISTS docs(
    id INTEGER PRIMARY KEY,
    path TEXT UNIQUE NOT NULL,
    name TEXT, ext TEXT, size INTEGER, mtime REAL,
    kind TEXT, category TEXT, title TEXT, description TEXT, tags TEXT,
    text TEXT, metadata TEXT, engine TEXT, analyzed_at TEXT, error TEXT,
    embedding BLOB
);
CREATE TABLE IF NOT EXISTS fingerprints(
    path TEXT PRIMARY KEY, size INTEGER, mtime REAL, dhash TEXT
);
CREATE VIRTUAL TABLE IF NOT EXISTS docs_fts USING fts5(
    name, title, description, tags, text,
    content='docs', content_rowid='id', tokenize='unicode61 remove_diacritics 2'
);
CREATE TRIGGER IF NOT EXISTS docs_ai AFTER INSERT ON docs BEGIN
  INSERT INTO docs_fts(rowid, name, title, description, tags, text)
  VALUES (new.id, new.name, new.title, new.description, new.tags, new.text);
END;
CREATE TRIGGER IF NOT EXISTS docs_ad AFTER DELETE ON docs BEGIN
  INSERT INTO docs_fts(docs_fts, rowid, name, title, description, tags, text)
  VALUES ('delete', old.id, old.name, old.title, old.description, old.tags, old.text);
END;
CREATE TRIGGER IF NOT EXISTS docs_au AFTER UPDATE ON docs BEGIN
  INSERT INTO docs_fts(docs_fts, rowid, name, title, description, tags, text)
  VALUES ('delete', old.id, old.name, old.title, old.description, old.tags, old.text);
  INSERT INTO docs_fts(rowid, name, title, description, tags, text)
  VALUES (new.id, new.name, new.title, new.description, new.tags, new.text);
END;
"""

STOPWORDS = {
    "il", "lo", "la", "i", "gli", "le", "un", "uno", "una", "di", "da", "in", "con", "su", "per",
    "tra", "fra", "del", "della", "dei", "delle", "degli", "dello", "al", "alla", "ai", "alle",
    "nel", "nella", "nei", "nelle", "sul", "sulla", "e", "o", "che", "chi", "cui", "non", "mi",
    "ti", "si", "ci", "vi", "file", "files", "documento", "documenti", "trova", "cerca", "dove",
    "mostrami", "sono", "the", "of", "and", "a", "an", "to", "my", "miei", "mie", "mio", "mia",
}

FIELDS = ("path", "name", "ext", "size", "mtime", "kind", "category", "title", "description",
          "tags", "text", "metadata", "engine", "analyzed_at", "error")


def query_terms(query: str) -> list[str]:
    import unicodedata

    q = unicodedata.normalize("NFKD", query.lower())
    q = "".join(c for c in q if not unicodedata.combining(c))
    return [t for t in re.findall(r"\w+", q) if len(t) > 1 and t not in STOPWORDS]


def pack(vec: list[float]) -> bytes:
    return array("f", vec).tobytes()


def unpack(blob: bytes | None) -> array | None:
    if not blob:
        return None
    a = array("f")
    a.frombytes(blob)
    return a


def cosine(a, b) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


class Index:
    """Indice cifrato: SQLite in memoria, persistito come blob AES-GCM (`index.db.enc`)."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or app_home() / "index.db.enc"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(":memory:", check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._dirty = 0
        self._last_flush = time.monotonic()
        legacy = self.path.with_name("index.db")
        if self.path.exists():
            self._load(crypto.read_encrypted(self.path, PURPOSE))
        elif legacy.exists():
            self._migrate_plaintext(legacy)
        self._db.executescript(SCHEMA)
        self._db.commit()
        _OPEN.add(self)

    def _load(self, data: bytes) -> None:
        """Carica un DB serializzato.

        Un DB nato in modalità WAL ha i byte 18-19 dell'header = 2: in memoria il WAL non
        esiste e SQLite fallirebbe ('unable to open database file'), quindi li riportiamo
        a 1 (journal classico).
        """
        if len(data) >= 100 and data[:16] == b"SQLite format 3\x00" and 2 in (data[18], data[19]):
            buf = bytearray(data)
            buf[18] = buf[19] = 1
            data = bytes(buf)
        self._db.deserialize(data)

    def _migrate_plaintext(self, legacy: Path) -> None:
        src = sqlite3.connect(legacy)
        try:
            src.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            src.backup(self._db)
        finally:
            src.close()
        self._load(self._db.serialize())
        self._dirty = 1
        self.flush(force=True)
        for p in (legacy, legacy.with_name("index.db-wal"), legacy.with_name("index.db-shm")):
            if p.exists():
                crypto.secure_delete(p)

    @contextmanager
    def conn(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            before = self._db.total_changes
            try:
                yield self._db
                self._db.commit()
            except Exception:
                self._db.rollback()
                raise
            if self._db.total_changes != before:
                self._dirty += 1
        self.flush()

    def flush(self, force: bool = False) -> None:
        with self._lock:
            if not self._dirty:
                return
            if not force and self._dirty < FLUSH_EVERY_CHANGES and \
                    time.monotonic() - self._last_flush < FLUSH_EVERY_SECONDS:
                return
            data = self._db.serialize()
            crypto.write_encrypted(self.path, data, PURPOSE)
            self._dirty = 0
            self._last_flush = time.monotonic()

    # ----------------------------------------------------------------- scrittura
    def upsert(self, doc: dict[str, Any], embedding: list[float] | None = None) -> None:
        row = {k: doc.get(k) for k in FIELDS}
        row["path"] = str(row["path"])
        for k in ("tags", "metadata"):
            if not isinstance(row[k], str):
                row[k] = json.dumps(row[k] or ([] if k == "tags" else {}), ensure_ascii=False)
        row["analyzed_at"] = row["analyzed_at"] or datetime.now().isoformat(timespec="seconds")
        cols = ", ".join(FIELDS) + ", embedding"
        marks = ", ".join("?" for _ in FIELDS) + ", ?"
        updates = ", ".join(f"{k}=excluded.{k}" for k in FIELDS[1:]) + ", embedding=excluded.embedding"
        with self.conn() as c:
            c.execute(f"INSERT INTO docs({cols}) VALUES ({marks}) "
                      f"ON CONFLICT(path) DO UPDATE SET {updates}",
                      [row[k] for k in FIELDS] + [pack(embedding) if embedding else None])

    def move(self, old: str, new: str) -> None:
        """Aggiorna il percorso dopo uno spostamento (organize/dedupe/undo)."""
        with self.conn() as c:
            c.execute("DELETE FROM docs WHERE path=?", (new,))
            c.execute("UPDATE docs SET path=?, name=? WHERE path=?", (new, Path(new).name, old))
            c.execute("DELETE FROM fingerprints WHERE path=?", (new,))
            c.execute("UPDATE fingerprints SET path=? WHERE path=?", (new, old))

    def remove_missing(self, root: Path) -> int:
        removed = 0
        with self.conn() as c:
            for r in c.execute("SELECT path FROM docs WHERE path LIKE ?", (f"{root}/%",)).fetchall():
                if not Path(r["path"]).exists():
                    c.execute("DELETE FROM docs WHERE path=?", (r["path"],))
                    removed += 1
            for r in c.execute("SELECT path FROM fingerprints WHERE path LIKE ?", (f"{root}/%",)).fetchall():
                if not Path(r["path"]).exists():
                    c.execute("DELETE FROM fingerprints WHERE path=?", (r["path"],))
        return removed

    # ------------------------------------------------- impronte visive (foto simili)
    def fingerprints(self, paths: list[str]) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        with self.conn() as c:
            for i in range(0, len(paths), 500):
                chunk = paths[i:i + 500]
                q = f"SELECT * FROM fingerprints WHERE path IN ({','.join('?' * len(chunk))})"
                for r in c.execute(q, chunk):
                    out[r["path"]] = {"size": r["size"], "mtime": r["mtime"] or 0,
                                      "dhash": int(r["dhash"], 16) if r["dhash"] else None}
        return out

    def set_fingerprints(self, rows: list[tuple[str, int, float, int | None]]) -> None:
        with self.conn() as c:
            c.executemany("INSERT OR REPLACE INTO fingerprints(path, size, mtime, dhash) VALUES (?,?,?,?)",
                          [(p, s, m, f"{h:016x}" if h is not None else None) for p, s, m, h in rows])

    def texts(self, paths: list[str]) -> dict[str, str]:
        """Testo estratto dei file indicizzati (per il confronto tra documenti)."""
        out: dict[str, str] = {}
        with self.conn() as c:
            for i in range(0, len(paths), 500):
                chunk = paths[i:i + 500]
                q = f"SELECT path, text FROM docs WHERE text IS NOT NULL AND text != '' AND path IN ({','.join('?' * len(chunk))})"
                for r in c.execute(q, chunk):
                    out[r["path"]] = r["text"]
        return out

    # ------------------------------------------------------------------- lettura
    def get(self, path: Path | str) -> dict[str, Any] | None:
        with self.conn() as c:
            r = c.execute("SELECT * FROM docs WHERE path=?", (str(path),)).fetchone()
        return self._row(r) if r else None

    def fresh(self, path: Path, size: int, mtime: float) -> bool:
        with self.conn() as c:
            r = c.execute("SELECT size, mtime, error FROM docs WHERE path=?", (str(path),)).fetchone()
        return bool(r) and r["size"] == size and abs((r["mtime"] or 0) - mtime) < 1 and not r["error"]

    def under(self, root: Path) -> dict[str, dict[str, Any]]:
        with self.conn() as c:
            rows = c.execute("SELECT path, category, title, description FROM docs WHERE path LIKE ?",
                             (f"{root}/%",)).fetchall()
        return {r["path"]: dict(r) for r in rows}

    def stats(self, root: Path | None = None) -> dict[str, Any]:
        where, args = ("WHERE path LIKE ?", (f"{root}/%",)) if root else ("", ())
        with self.conn() as c:
            r = c.execute(f"SELECT COUNT(*) n, SUM(embedding IS NOT NULL) e, SUM(error IS NOT NULL) err "
                          f"FROM docs {where}", args).fetchone()
        return {"documents": r["n"] or 0, "with_embedding": r["e"] or 0, "errors": r["err"] or 0}

    def fts(self, query: str, root: Path | None = None, limit: int = 50) -> list[tuple[str, float, str]]:
        terms = query_terms(query)
        if not terms:
            return []
        match = " OR ".join(f'"{t}"*' for t in terms)
        sql = ("SELECT d.path, bm25(docs_fts, 8.0, 6.0, 4.0, 5.0, 1.0) AS score, "
               "snippet(docs_fts, 4, '«', '»', '…', 12) AS snip "
               "FROM docs_fts JOIN docs d ON d.id = docs_fts.rowid WHERE docs_fts MATCH ?")
        args: list[Any] = [match]
        if root:
            sql += " AND d.path LIKE ?"
            args.append(f"{root}/%")
        sql += " ORDER BY score LIMIT ?"
        args.append(limit)
        with self.conn() as c:
            try:
                rows = c.execute(sql, args).fetchall()
            except sqlite3.OperationalError:
                return []
        return [(r["path"], -r["score"], r["snip"] or "") for r in rows]

    def semantic(self, vector: list[float], root: Path | None = None, limit: int = 50,
                 min_score: float = 0.35) -> list[tuple[str, float]]:
        sql, args = "SELECT path, embedding FROM docs WHERE embedding IS NOT NULL", []
        if root:
            sql += " AND path LIKE ?"
            args.append(f"{root}/%")
        with self.conn() as c:
            rows = c.execute(sql, args).fetchall()
        q = array("f", vector)
        scored = [(r["path"], cosine(q, unpack(r["embedding"]))) for r in rows]
        scored = [s for s in scored if s[1] >= min_score]
        scored.sort(key=lambda t: -t[1])
        return scored[:limit]

    @staticmethod
    def _row(r: sqlite3.Row) -> dict[str, Any]:
        d = dict(r)
        d.pop("embedding", None)
        for k, default in (("tags", []), ("metadata", {})):
            try:
                d[k] = json.loads(d[k]) if d.get(k) else default
            except json.JSONDecodeError:
                d[k] = default
        return d
