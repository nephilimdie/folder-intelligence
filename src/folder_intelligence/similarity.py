"""Elementi simili (non identici): foto quasi uguali, documenti con lo stesso testo, versioni.

A differenza dei duplicati (stessi byte), qui si cercano file che *sembrano* la stessa cosa:

- **foto**: impronta visiva dHash a 64 bit. L'immagine viene ridotta a 9×8 pixel in scala di
  grigi con `sips` (legge anche HEIC/RAW) e si confronta la luminosità dei pixel vicini.
  Due foto sono simili se le impronte differiscono di pochi bit (distanza di Hamming): così
  si trovano foto ridimensionate, ricompresse, convertite (HEIC→JPG), raffiche e quasi-scatti.
  Le impronte vengono salvate nell'indice cifrato: la seconda ricerca è istantanea.
- **documenti**: testo estratto dall'indice (serve "indicizza"). Si confrontano gruppi di 5
  parole consecutive: stesso documento in formati diversi (docx/pdf), bozze, versioni.
- **versioni per nome**: "contratto_v2.pdf", "contratto finale.pdf", "contratto (1).docx"…

Sono solo *suggerimenti*: nulla viene spostato o cancellato.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
import threading
import unicodedata
import uuid
import zlib
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .models import FileInfo, format_size
from .scanner import Progress, looks_like_copy
from .threads import Cancelled, parallel_map

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp", ".tif", ".tiff", ".gif", ".bmp",
             ".cr2", ".cr3", ".nef", ".arw", ".dng", ".raf", ".orf", ".rw2"}
DOC_FAMILIES = {
    "testo": {".pdf", ".doc", ".docx", ".odt", ".pages", ".rtf", ".txt", ".md"},
    "foglio": {".xls", ".xlsx", ".numbers", ".ods", ".csv"},
    "presentazione": {".ppt", ".pptx", ".key", ".odp"},
}
FAMILY_OF = {ext: fam for fam, exts in DOC_FAMILIES.items() for ext in exts}

# soglie (distanza di Hamming su 64 bit)
STRICT, NORMAL, LOOSE = 5, 9, 13
MAX_IMAGES = 15_000
TEXT_SIMILAR = 0.5        # Jaccard sui gruppi di parole
TEXT_CONTAINED = 0.8      # quota del documento più corto presente nell'altro
SHINGLE = 5
SAMPLE_MOD = 4            # si usa 1 gruppo di parole su 4 (scelta deterministica via hash)
MIN_SHINGLES = 8
MIN_CONTAINED = 25        # "uno contiene l'altro" solo se il più corto ha almeno ~100 parole
COMMON_SHINGLE = 30       # gruppi di parole presenti in troppi documenti = formule standard


@dataclass
class SimilarGroup:
    kind: str                     # "foto" | "documenti"
    files: list[FileInfo]
    reason: str
    score: float                  # 0..1, 1 = praticamente identici
    keep: FileInfo | None = None  # suggerimento di quale tenere
    details: dict[str, str] = field(default_factory=dict)  # path → nota (es. "distanza 3")

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "reason": self.reason, "score": round(self.score, 3),
                "suggested_keep": str(self.keep.path) if self.keep else None,
                "files": [{"path": str(f.path), "size_bytes": f.size_bytes, "size": format_size(f.size_bytes),
                           "modified": f.modified_iso, "note": self.details.get(str(f.path), "")}
                          for f in self.files]}


@dataclass
class SimilarResult:
    groups: list[SimilarGroup]
    images_checked: int = 0
    images_skipped: int = 0
    documents_checked: int = 0
    documents_not_indexed: int = 0
    errors: list[str] = field(default_factory=list)


# ------------------------------------------------------------------ union-find
class _Groups:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def find(self, x: str) -> str:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra

    def clusters(self) -> list[list[str]]:
        out: dict[str, list[str]] = defaultdict(list)
        for x in self.parent:
            out[self.find(x)].append(x)
        return [v for v in out.values() if len(v) > 1]


# ------------------------------------------------------------------ foto
def parse_bmp_gray(data: bytes) -> list[list[float]]:
    """Pixel in scala di grigi da un BMP non compresso a 24/32 bit (output di sips)."""
    if data[:2] != b"BM":
        raise ValueError("non è un BMP")
    offset = int.from_bytes(data[10:14], "little")
    width = int.from_bytes(data[18:22], "little", signed=True)
    height = int.from_bytes(data[22:26], "little", signed=True)
    bpp = int.from_bytes(data[28:30], "little")
    if bpp not in (24, 32):
        raise ValueError(f"BMP a {bpp} bit non supportato")
    step = bpp // 8
    row_size = ((bpp * width + 31) // 32) * 4
    rows = []
    for y in range(abs(height)):
        start = offset + y * row_size
        row = []
        for x in range(width):
            b, g, r = data[start + x * step: start + x * step + 3]
            row.append(0.299 * r + 0.587 * g + 0.114 * b)
        rows.append(row)
    if height > 0:  # BMP "bottom-up"
        rows.reverse()
    return rows


def dhash_from_gray(rows: list[list[float]]) -> int | None:
    """dHash 64 bit da 8 righe × 9 colonne. None se l'immagine è uniforme (inutile confrontarla)."""
    flat = [v for row in rows for v in row]
    if max(flat) - min(flat) < 6:
        return None
    h = 0
    for row in rows[:8]:
        for x in range(8):
            h = (h << 1) | (row[x] > row[x + 1])
    return h


def image_dhash(path: Path, tmpdir: str) -> int | None:
    out = os.path.join(tmpdir, uuid.uuid4().hex + ".bmp")
    try:
        r = subprocess.run(["sips", "-s", "format", "bmp", "-z", "8", "9", str(path), "--out", out],
                           capture_output=True, timeout=30)
        if r.returncode or not os.path.exists(out):
            raise ValueError("immagine non leggibile")
        with open(out, "rb") as f:
            return dhash_from_gray(parse_bmp_gray(f.read()))
    finally:
        try:
            os.unlink(out)
        except OSError:
            pass


def hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def _image_groups(images: list[FileInfo], hashes: dict[str, int], threshold: int,
                  cancel: threading.Event | None) -> list[SimilarGroup]:
    items = [(str(f.path), hashes[str(f.path)]) for f in images if str(f.path) in hashes]
    by_path = {str(f.path): f for f in images}
    uf = _Groups()
    best: dict[tuple[str, str], int] = {}
    n = len(items)
    for i in range(n):
        if cancel is not None and i % 200 == 0 and cancel.is_set():
            raise Cancelled()
        pi, hi = items[i]
        for j in range(i + 1, n):
            d = (hi ^ items[j][1]).bit_count()
            if d <= threshold:
                uf.union(pi, items[j][0])
                best[(pi, items[j][0])] = d
    groups = []
    for cluster in uf.clusters():
        files = sorted((by_path[p] for p in cluster), key=lambda f: (-f.size_bytes, f.modified))
        # file più pesante ≈ qualità maggiore; a parità (±5%) preferisce il nome senza "(1)"/"copia"
        biggest = files[0].size_bytes
        keep = min((f for f in files if f.size_bytes >= biggest * 0.95),
                   key=lambda f: (looks_like_copy(f), f.modified))
        files.remove(keep)
        files.insert(0, keep)
        kh = hashes[str(keep.path)]
        dists = {str(f.path): hamming(kh, hashes[str(f.path)]) for f in files[1:]}
        details = {p: ("praticamente identica" if d <= 2 else f"differenza {d}/64") for p, d in dists.items()}
        details[str(keep.path)] = "⭐ qualità migliore"
        worst = max(dists.values()) if dists else 0
        reason = "foto praticamente identiche" if worst <= 2 else "foto molto simili" if worst <= STRICT \
            else "foto simili (stessa scena, ritagli o modifiche)"
        groups.append(SimilarGroup("foto", files, reason, 1 - worst / 64, keep, details))
    return groups


# ------------------------------------------------------------------ documenti
def _words(text: str) -> list[str]:
    t = unicodedata.normalize("NFKD", text.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.findall(r"[^\W_]+", t)  # "_" separa le parole come lo spazio


def shingles(text: str) -> set[int]:
    w = _words(text)
    out = set()
    for i in range(max(0, len(w) - SHINGLE + 1)):
        h = zlib.crc32(" ".join(w[i:i + SHINGLE]).encode())
        if h % SAMPLE_MOD == 0:
            out.add(h)
    return out


_SEP = r"(?:^|[\s_.\-]+)"          # "_" è un carattere di parola: \b non basta
_END = r"(?=$|[\s_.\-])"
_VERSION_RE = re.compile(
    _SEP + r"(?:v|ver|vers|versione|version|rev|revisione)[\s_.\-]?\d+(?:[._]\d+)*" + _END
    + "|" + _SEP + r"(?:final|finale|def|definitivo|definitiva|bozza|draft|old|vecchio|vecchia|nuovo|nuova|new"
    r"|copia|copy|corretto|corretta|aggiornato|aggiornata|modificato|modificata|firmato|firmata|ok)" + _END
    + r"|\s*\(\d+\)|\s\d{1,2}$",  # "(1)" e "nome 2" (Finder); NON "_2": spesso è una numerazione
    re.IGNORECASE)
_GENERIC = {"documento", "document", "untitled", "senza titolo", "nuovo documento", "file", "scan",
            "scansione", "readme", "index", "fattura", "invoice", "image", "immagine", "test", "prova"}


def version_key(f: FileInfo) -> tuple[str, str] | None:
    """('contratto affitto', 'testo') per 'Contratto_affitto_v2 FINALE.docx'; None se non è una versione."""
    fam = FAMILY_OF.get(f.extension)
    if not fam:
        return None
    stem = f.path.name[: -len(f.path.suffix)] if f.path.suffix else f.path.name
    norm = stem
    for _ in range(5):
        new = _VERSION_RE.sub("", norm).strip(" _-.")
        if new == norm:
            break
        norm = new
    if norm == stem.strip(" _-."):
        return None  # nessun segno di versione nel nome
    key = " ".join(_words(norm))
    if not _meaningful(key):
        return None
    return key, fam


def version_stem(f: FileInfo) -> tuple[str, str] | None:
    """Come version_key ma anche per i file "base" senza segni di versione (contratto.pdf)."""
    fam = FAMILY_OF.get(f.extension)
    if not fam:
        return None
    stem = f.path.name[: -len(f.path.suffix)] if f.path.suffix else f.path.name
    key = " ".join(_words(stem))
    return (key, fam) if _meaningful(key) else None


def _meaningful(key: str) -> bool:
    """Un nome dice qualcosa solo se ha parole vere ("04 2024" o "img 1234" no)."""
    letters = sum(c.isalpha() for c in key)
    return letters >= 3 and len(key) >= 4 and key not in _GENERIC


def _document_groups(docs: list[FileInfo], texts: dict[str, str],
                     cancel: threading.Event | None) -> list[SimilarGroup]:
    by_path = {str(f.path): f for f in docs}
    uf = _Groups()
    notes: dict[tuple[str, str], str] = {}
    scores: dict[tuple[str, str], float] = {}

    # 1) testo in comune
    sets: dict[str, set[int]] = {}
    for p, t in texts.items():
        if p in by_path:
            s = shingles(t)
            if len(s) >= MIN_SHINGLES:
                sets[p] = s
    inverted: dict[int, list[str]] = defaultdict(list)
    for p, s in sets.items():
        for h in s:
            inverted[h].append(p)
    shared: dict[tuple[str, str], int] = defaultdict(int)
    for i, (h, paths) in enumerate(inverted.items()):
        if cancel is not None and i % 5000 == 0 and cancel.is_set():
            raise Cancelled()
        if 1 < len(paths) <= COMMON_SHINGLE:
            paths.sort()
            for a in range(len(paths)):
                for b in range(a + 1, len(paths)):
                    shared[(paths[a], paths[b])] += 1
    for (a, b), k in shared.items():
        na, nb = len(sets[a]), len(sets[b])
        jacc = k / (na + nb - k)
        contained = k / min(na, nb)
        if jacc >= TEXT_SIMILAR:
            uf.union(a, b)
            notes[(a, b)] = "testo identico" if jacc > 0.97 else f"testo uguale al {round(jacc * 100)}%"
            scores[(a, b)] = jacc
        elif contained >= TEXT_CONTAINED and min(na, nb) >= MIN_CONTAINED:
            uf.union(a, b)
            notes[(a, b)] = "uno contiene l'altro (bozza/estratto)"
            scores[(a, b)] = contained * 0.9

    # 2) versioni dello stesso nome, nella stessa cartella ("contratto.pdf", "contratto v2.pdf")
    versioned: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    for f in docs:
        if key := version_key(f):
            versioned[(str(f.path.parent), *key)].append(str(f.path))
    for f in docs:  # aggiunge l'eventuale "contratto.pdf" base
        if (key := version_stem(f)) and (k := (str(f.path.parent), *key)) in versioned \
                and str(f.path) not in versioned[k]:
            versioned[k].append(str(f.path))

    def different_text(a: str, b: str) -> bool:
        if a not in sets or b not in sets:
            return False  # testo non letto: ci fidiamo del nome
        k = len(sets[a] & sets[b])
        return k / (len(sets[a]) + len(sets[b]) - k) < 0.2

    for ps in versioned.values():
        for i, a in enumerate(ps):
            for b in ps[i + 1:]:
                pair = tuple(sorted((a, b)))
                if pair in notes or different_text(a, b):
                    continue
                uf.union(a, b)
                notes[pair] = "versioni dello stesso documento (dal nome)"
                scores[pair] = 0.6

    groups = []
    for cluster in uf.clusters():
        files = sorted((by_path[p] for p in cluster), key=lambda f: -f.modified)
        keep = files[0]  # il più recente = di solito la versione buona
        members = set(cluster)
        pair_notes = {k: v for k, v in notes.items() if k[0] in members}
        text_scores = [scores[k] for k, v in pair_notes.items() if v.startswith("testo")]
        kinds = {("nome" if "nome" in v else "contiene" if "contiene" in v else "testo") for v in pair_notes.values()}
        if kinds == {"testo"}:
            low = min(text_scores)
            reasons = ["testo identico" if low > 0.97 else f"testo uguale al {round(low * 100)}% o più"]
        elif kinds == {"nome"}:
            reasons = ["versioni dello stesso documento (dal nome)"]
        elif kinds == {"contiene"}:
            reasons = ["un documento contiene l'altro (bozza/estratto/aggiornamento)"]
        else:
            reasons = ["stesso documento in versioni o formati diversi"]
        details = {str(keep.path): "⭐ il più recente"}
        for (a, b), note in pair_notes.items():
            for p in (a, b):
                if p != str(keep.path):
                    details.setdefault(p, note)
        score = max((scores[k] for k in pair_notes), default=0.5)
        groups.append(SimilarGroup("documenti", files, reasons[0] if reasons else "documenti simili",
                                   score, keep, details))
    return groups


# ------------------------------------------------------------------ entry point
def find_similar(files: list[FileInfo], index: Any = None, kind: str = "all", threshold: int = NORMAL,
                 progress: Progress | None = None, cancel: threading.Event | None = None,
                 workers: int = 4) -> SimilarResult:
    """Cerca gruppi di file simili. `kind`: 'all' | 'images' | 'documents'."""
    result = SimilarResult(groups=[])
    local = [f for f in files if not f.cloud_only and f.size_bytes > 0]

    if kind in ("all", "images"):
        images = sorted((f for f in local if f.extension in IMAGE_EXT), key=lambda f: -f.modified)
        if len(images) > MAX_IMAGES:
            result.images_skipped = len(images) - MAX_IMAGES
            images = images[:MAX_IMAGES]
        cached = index.fingerprints([str(f.path) for f in images]) if index is not None else {}
        hashes: dict[str, int] = {}
        todo = []
        for f in images:
            c = cached.get(str(f.path))
            if c and c["size"] == f.size_bytes and abs(c["mtime"] - f.modified) < 1:
                if c["dhash"] is not None:
                    hashes[str(f.path)] = c["dhash"]
            else:
                todo.append(f)
        total = len(images)
        done = total - len(todo)
        if progress:
            progress("fingerprint", done, total)
        new: list[tuple[str, int, float, int | None]] = []
        with tempfile.TemporaryDirectory(prefix="fi-similar-") as tmp:
            for f, h, err in parallel_map(lambda f: image_dhash(f.path, tmp), todo,
                                          lambda f: 30.0, workers, cancel):
                done += 1
                if progress:
                    progress("fingerprint", done, total, f.name)
                if err:
                    result.errors.append(f"{f.path}: {err}")
                    continue
                new.append((str(f.path), f.size_bytes, f.modified, h))
                if h is not None:
                    hashes[str(f.path)] = h
                if index is not None and len(new) >= 200:
                    index.set_fingerprints(new)
                    new = []
        if index is not None and new:
            index.set_fingerprints(new)
        result.images_checked = len(hashes)
        if progress:
            progress("compare", 0, None, "confronto le impronte")
        result.groups += _image_groups(images, hashes, threshold, cancel)

    if kind in ("all", "documents"):
        docs = [f for f in local if f.extension in FAMILY_OF]
        texts = index.texts([str(f.path) for f in docs]) if index is not None else {}
        result.documents_checked = len(docs)
        result.documents_not_indexed = sum(1 for f in docs if str(f.path) not in texts)
        if progress:
            progress("compare", 0, None, "confronto i documenti")
        result.groups += _document_groups(docs, texts, cancel)

    result.groups.sort(key=lambda g: (-len(g.files), -g.score))
    return result
