"""Scansione cartelle e rilevamento duplicati.

La scansione legge SOLO i metadati (nome, dimensione, data): è veloce e non apre i file.
Il contenuto viene letto solo quando serve davvero (duplicati), in parallelo e con un
timeout per file, saltando i file presenti solo su iCloud.
"""

from __future__ import annotations

import ctypes
import hashlib
import os
import re
import struct
import sys
import stat as stat_mod
import threading
from collections import defaultdict
from pathlib import Path
from typing import Iterable, Optional, Protocol

from .models import DuplicateGroup, FileInfo, ScanResult
from .threads import Cancelled, parallel_map


class Progress(Protocol):
    """Callback di avanzamento: fase ('scan'|'hash'|'classify'|'analyze'), fatti, totale, dettaglio."""

    def __call__(self, phase: str, done: int, total: Optional[int], detail: str = "") -> None: ...


CHUNK = 1024 * 1024
PARTIAL = 64 * 1024
HASH_WORKERS = 4
MIN_READ_SPEED = 20 * 1024 * 1024  # byte/s attesi nel caso peggiore (disco lento)
SF_DATALESS = 0x40000000  # macOS: file "solo iCloud" (contenuto non scaricato)

SKIP_DIRS = {
    ".git", ".svn", ".hg", "node_modules", "__pycache__", ".venv", "venv",
    ".Trash", ".backup", ".folder_intelligence",
}
BUNDLE_SUFFIXES = {".app", ".photoslibrary", ".bundle", ".framework", ".pkg", ".xcodeproj",
                   ".musiclibrary", ".tvlibrary", ".imovielibrary", ".fcpbundle",
                   # documenti-pacchetto: per il Finder sono un file solo, spostarne l'interno li rompe
                   ".rtfd", ".pages", ".numbers", ".key", ".scriv", ".sparsebundle", ".logicx",
                   ".band", ".xcworkspace", ".playground", ".lproj"}


def is_cloud_only(st: os.stat_result) -> bool:
    return bool(getattr(st, "st_flags", 0) & SF_DATALESS)


def iter_files(root: Path, include_hidden: bool = False,
               cancel: threading.Event | None = None) -> Iterable[tuple[Path, os.stat_result]]:
    """Cammina `root` restituendo (path, lstat) dei file regolari (no symlink)."""
    for dirpath, dirnames, filenames in os.walk(root):
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        dirnames[:] = sorted(
            d for d in dirnames
            if d not in SKIP_DIRS
            and (include_hidden or not d.startswith("."))
            and Path(d).suffix.lower() not in BUNDLE_SUFFIXES
        )
        for name in sorted(filenames):
            if not include_hidden and name.startswith("."):
                continue
            p = Path(dirpath) / name
            try:
                st = p.lstat()
            except OSError:
                continue
            if stat_mod.S_ISREG(st.st_mode):
                yield p, st


def hash_file(path: Path, limit: int | None = None) -> str:
    h = hashlib.sha256()
    read = 0
    with open(path, "rb") as f:
        while True:
            size = CHUNK if limit is None else min(CHUNK, limit - read)
            if size <= 0:
                break
            block = f.read(size)
            if not block:
                break
            h.update(block)
            read += len(block)
    return h.hexdigest()


# Nomi tipici delle copie: "foto copia", "foto copy 2", "Copia di foto", "foto (1)", "foto 2" (Finder)
_COPY_RE = re.compile(
    r"^(?:copia di|copy of)\s|[\s_-](?:copia|copy|kopie|copie)(?:\s*\d+)?$|\s\(\d+\)$|\s\d{1,2}$",
    re.IGNORECASE)
# Cartelle dove di solito finiscono le copie "di passaggio"
_TRANSIENT_DIRS = {"downloads", "download", "scaricati", "desktop", "scrivania", "tmp", "temp"}


def looks_like_copy(f: FileInfo) -> bool:
    stem = f.path.name[: -len(f.path.suffix)] if f.path.suffix else f.path.name
    return bool(_COPY_RE.search(stem.strip()))


def _keep_priority(f: FileInfo) -> tuple:
    """Ordine per scegliere l'originale da tenere.

    1. nome che non sembra una copia ("foto 2", "(1)", "copia"…);
    2. fuori da cartelle di passaggio (Download, Scrivania…);
    3. il più vecchio; 4. il percorso più corto.
    """
    transient = any(part.lower() in _TRANSIENT_DIRS for part in f.path.parts[:-1])
    return (looks_like_copy(f), transient, f.modified, len(str(f.path)), str(f.path))


# --- identità fisica (macOS/APFS): hardlink e cloni condividono lo stesso spazio ---------
class _AttrList(ctypes.Structure):
    _fields_ = [("bitmapcount", ctypes.c_ushort), ("reserved", ctypes.c_uint16),
                ("commonattr", ctypes.c_uint32), ("volattr", ctypes.c_uint32),
                ("dirattr", ctypes.c_uint32), ("fileattr", ctypes.c_uint32),
                ("forkattr", ctypes.c_uint32)]


_ATTR_CMN_RETURNED_ATTRS = 0x80000000
_ATTR_CMNEXT_CLONEID = 0x100
_ATTR_CMNEXT_CLONE_REFCNT = 0x1000
_FSOPT_NOFOLLOW_EXTENDED = 0x1 | 0x20
_libc = None


def _clone_id(path: Path) -> int | None:
    """Clone id APFS se il file condivide i blocchi con altri (cp -c, duplica del Finder)."""
    global _libc
    if sys.platform != "darwin":
        return None
    try:
        if _libc is None:
            _libc = ctypes.CDLL(None, use_errno=True)
        al = _AttrList(5, 0, _ATTR_CMN_RETURNED_ATTRS, 0, 0, 0,
                       _ATTR_CMNEXT_CLONEID | _ATTR_CMNEXT_CLONE_REFCNT)
        buf = ctypes.create_string_buffer(64)
        if _libc.getattrlist(os.fsencode(path), ctypes.byref(al), buf, 64, _FSOPT_NOFOLLOW_EXTENDED):
            return None
        returned_fork = struct.unpack_from("5I", buf, 4)[4]
        if not (returned_fork & _ATTR_CMNEXT_CLONEID and returned_fork & _ATTR_CMNEXT_CLONE_REFCNT):
            return None
        clone_id, refcnt = struct.unpack_from("QI", buf, 24)
        return clone_id if refcnt > 1 else None
    except Exception:
        return None


def physical_id(f: FileInfo) -> str:
    clone = _clone_id(f.path)
    if clone is not None:
        return f"clone:{f.device}:{clone}"
    return f"inode:{f.device}:{f.inode}"


def _timeout(f: FileInfo, limit: int | None) -> float:
    size = f.size_bytes if limit is None else min(f.size_bytes, limit)
    return 15 + size / MIN_READ_SPEED


def find_duplicates(
    files: list[FileInfo],
    progress: Progress | None = None,
    cancel: threading.Event | None = None,
    errors: list[str] | None = None,
) -> list[DuplicateGroup]:
    """Trova duplicati: dimensione → hash dei primi 64KB → SHA-256 completo.

    Legge in parallelo con timeout per file; i file solo-iCloud o bloccati vengono saltati
    (e annotati in `errors`).
    """
    errors = errors if errors is not None else []
    by_size: dict[int, list[FileInfo]] = defaultdict(list)
    for f in files:
        if f.size_bytes > 0 and not f.cloud_only:
            by_size[f.size_bytes].append(f)

    # gli hardlink sono lo stesso file fisico: lo leggiamo una volta sola
    links: dict[tuple[int, int], list[FileInfo]] = defaultdict(list)
    candidates = []
    for g in by_size.values():
        if len(g) < 2:
            continue
        for f in g:
            key = (f.device, f.inode)
            if not f.inode or key not in links:
                candidates.append(f)
            if f.inode:
                links[key].append(f)

    def expand(members: list[FileInfo]) -> list[FileInfo]:
        out: list[FileInfo] = []
        for m in members:
            twins = links.get((m.device, m.inode), [m]) if m.inode else [m]
            for t in twins:
                t.sha256 = m.sha256
            out += twins
        return out

    def make_group(digest: str, members: list[FileInfo]) -> DuplicateGroup | None:
        for m in members:
            m.sha256 = digest
        members = expand(members)
        if len(members) < 2:
            return None
        for m in members:
            m.storage = physical_id(m)
        return DuplicateGroup(digest, sorted(members, key=_keep_priority))

    total = len(candidates)
    if progress:
        progress("hash", 0, total)

    # 1) hash parziale (primi 64KB) di tutti i candidati
    partial: dict[tuple[int, str], list[FileInfo]] = defaultdict(list)
    done = 0
    for f, digest, err in parallel_map(lambda f: hash_file(f.path, PARTIAL), candidates,
                                       lambda f: _timeout(f, PARTIAL), HASH_WORKERS, cancel):
        done += 1
        if progress:
            progress("hash", done, total, f.name)
        if digest is None:
            errors.append(f"{f.path}: {err}")
            continue
        partial[(f.size_bytes, digest)].append(f)

    # 2) hash completo solo dove i primi 64KB coincidono (e il file è più lungo)
    groups: list[DuplicateGroup] = []
    need_full: list[FileInfo] = []
    for (size, digest), members in partial.items():
        if len(expand(members)) < 2:
            continue
        if size <= PARTIAL:  # i primi 64KB sono tutto il file
            if g := make_group(digest, members):
                groups.append(g)
        else:
            need_full += members

    if need_full:
        full: dict[str, list[FileInfo]] = defaultdict(list)
        total_full = len(need_full)
        done = 0
        if progress:
            progress("hash_full", 0, total_full)
        for f, digest, err in parallel_map(lambda f: hash_file(f.path), need_full,
                                           lambda f: _timeout(f, None), HASH_WORKERS, cancel):
            done += 1
            if progress:
                progress("hash_full", done, total_full, f.name)
            if digest is None:
                errors.append(f"{f.path}: {err}")
                continue
            f.sha256 = digest
            full[digest].append(f)
        for digest, members in full.items():
            if g := make_group(digest, members):
                groups.append(g)

    groups.sort(key=lambda g: -g.wasted_bytes)
    return groups


def scan_directory(
    root: str | Path,
    include_hidden: bool = False,
    progress: Progress | None = None,
    cancel: threading.Event | None = None,
    duplicates: bool = False,
) -> ScanResult:
    """Scansiona `root` leggendo solo i metadati. Con `duplicates=True` cerca anche i doppioni."""
    root = Path(root).expanduser().resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"La cartella {root} non esiste o non è una cartella.")

    files: list[FileInfo] = []
    errors: list[str] = []
    if progress:
        progress("scan", 0, None, str(root))
    for p, st in iter_files(root, include_hidden, cancel):
        files.append(FileInfo(path=p, size_bytes=st.st_size, modified=st.st_mtime,
                              cloud_only=is_cloud_only(st), device=st.st_dev, inode=st.st_ino))
        if progress and len(files) % 200 == 0:
            progress("scan", len(files), None, str(p.parent))
    if progress:
        progress("scan", len(files), len(files), "")

    result = ScanResult(root=root, files=files, duplicates=[], errors=errors)
    if duplicates:
        compute_duplicates(result, progress, cancel)
    return result


def compute_duplicates(scan: ScanResult, progress: Progress | None = None,
                       cancel: threading.Event | None = None) -> ScanResult:
    """Calcola (una volta sola) i duplicati di una scansione già fatta."""
    if not scan.duplicates_checked:
        scan.duplicates = find_duplicates(scan.files, progress, cancel, scan.errors)
        scan.duplicates_checked = True
    return scan
