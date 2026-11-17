"""Document intelligence: comprensione del contenuto di qualsiasi file + ricerca.

Pipeline per file:
  1. metadati (stat + Spotlight `mdls`: provenienza download, autore, pagine, GPS…)
  2. estrazione con il primo estrattore adatto (vedi cartella extractors/)
  3. comprensione AI (modello testuale): categoria, titolo, descrizione, tag, date, importi
  4. embedding (bge-m3) per la ricerca semantica
  5. salvataggio nell'indice (incrementale)

Ricerca ibrida (reciprocal rank fusion): FTS5 + semantica + Spotlight + nome file.
"""

from __future__ import annotations

import os
import re

import asyncio
import atexit
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import shell
from .ai import AIEngine
from .classifier import CATEGORIES, RuleClassifier
from .extractors import ExtractContext, Extraction, Extractor, load_extractors
from .engines.base import TEXT, VISION
from .i18n import N_, _, language_name
from .index import Index, query_terms
from .threads import Cancelled, Timeout

EXTRACT_TIMEOUT = 240  # secondi massimi per file (OCR + modello visivo inclusi)
MAX_FILE_BYTES = 2 * 1024 ** 3  # oltre: solo metadati
# Soglie per bge-m3, misurate su documenti reali: pertinenti ≈ 0.55–0.71, estranei ≤ 0.50.
PHASE_BATCH = int(os.environ.get("FI_PHASE_BATCH", "16"))  # file per blocco (cambio modello)
VISUAL_EXT = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp", ".tif", ".tiff", ".gif", ".bmp",
              ".pdf", ".mp4", ".mov", ".m4v", ".mkv", ".avi"}  # possono richiedere il modello visivo
SEMANTIC_MIN = float(os.environ.get("FI_SEMANTIC_MIN", "0.53"))  # ricerca "cerca": precisa
SEMANTIC_MIN_LOOSE = 0.40   # domande ("chiedi"): meglio qualche fonte in più, sceglie il modello
SEMANTIC_MARGIN = 0.07      # distanza massima dal risultato migliore


# File che non sono documenti: dati interni di app, lock, frammenti con nomi-identificativo.
# Si contano nelle scansioni (spazio, duplicati) ma non si "leggono" né si riassumono.
NOT_CONTENT_EXT = {".abcdp", ".abcddb", ".abcdg", ".abcds", ".lockfile", ".lock", ".tmp", ".temp", ".part",
                   ".crdownload", ".download", ".partial", ".swp", ".db-wal", ".db-shm", ".sqlite-wal",
                   ".sqlite-shm", ".plist", ".icloud", ".localized", ".ds_store", ".ini", ".log", ".cache",
                   ".map", ".pyc", ".o", ".class", ".dylib", ".so", ".dll"}
_ID_NAME = re.compile(r"^[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}$|^[0-9a-f]{16,}$", re.I)


def is_content(path: Path) -> bool:
    """True se vale la pena leggerlo e capirlo (documenti, immagini, media, archivi, testo)."""
    ext = path.suffix.lower()
    if ext in NOT_CONTENT_EXT:
        return False
    if not ext and _ID_NAME.match(path.name):
        return False  # es. "4A1865C8-3F9E-…": frammenti interni di app
    return True


def required_terms(n: int) -> int:
    """Quante parole della ricerca devono comparire nel testo: 1 su 1, 2 su 2-3, metà oltre."""
    return 1 if n <= 1 else 2 if n <= 3 else (n + 1) // 2

def expand_system() -> str:
    return ("Expand the user's file search request. Answer ONLY with JSON: "
            '{"keywords": ["6-10 keywords: synonyms and related terms that could appear in the documents '
            'searched for, in the language of the request, plus their Italian and English equivalents"]}')


def summary_system() -> str:
    """Istruzioni per capire un file; titolo, descrizione e tag nella lingua dell'utente."""
    language = language_name()
    return (
        "You are an archivist. You receive the content extracted from a file (text, OCR, visual "
        "description, metadata). Answer ONLY with valid JSON:\n"
        '{"category": "<one of: ' + ", ".join(CATEGORIES) + '>", '
        f'"title": "<short title in {language}, max 8 words>", '
        f'"description": "<one sentence in {language}, max 25 words: what it is and what it is about>", '
        f'"doc_type": "<generic type in 1-2 words, in {language}: receipt, invoice, contract, policy, '
        'minutes, letter, cv, bank statement, photo, screenshot, presentation, code, …>", '
        f'"tags": ["<3-6 lowercase keywords in {language}: people/companies, places, topics, year>"], '
        '"date": "<main date YYYY-MM-DD or null>", "amount": "<main amount with currency or null>"}'
    )


@dataclass
class AnalyzeStats:
    analyzed: int = 0
    skipped_fresh: int = 0
    skipped_cloud: int = 0
    skipped_noncontent: int = 0
    errors: list[str] = field(default_factory=list)
    engines: dict[str, int] = field(default_factory=dict)
    seconds: float = 0.0


@dataclass
class Hit:
    path: str
    score: float
    doc: dict[str, Any] | None
    snippet: str = ""
    sources: list[str] = field(default_factory=list)


class DocumentIntelligence:
    def __init__(self, ai: AIEngine | None = None, index: Index | None = None, use_vision: bool = True,
                 extractors: list[Extractor] | None = None) -> None:
        self.ai = ai
        self.index = index or Index()
        self.use_vision = use_vision
        self.extractors = extractors if extractors is not None else load_extractors()
        self.rules = RuleClassifier()
        self._tmp = Path(tempfile.mkdtemp(prefix="folder-intelligence-"))  # permessi 0700
        _TEMP_DIRS.append(self._tmp)
        self._expansions: dict[str, list[str]] = {}
        # modello per i riassunti dell'indicizzazione (FI_INDEX_MODEL; "" = quello principale)
        self.index_model: str | None = os.environ.get("FI_INDEX_MODEL", "qwen2.5:7b") or None

    # --------------------------------------------------------------- estrazione
    async def extract(self, path: Path, vision: bool | None = None) -> Extraction:
        use_vision = self.use_vision if vision is None else (vision and self.use_vision)
        ctx = ExtractContext(ai=self.ai, use_vision=use_vision, tmpdir=self._tmp)
        last_error = ""
        for ex in self.extractors:
            if not ex.supports(path) or not await ex.available(ctx):
                continue
            try:
                res = await asyncio.wait_for(ex.extract(path, ctx), EXTRACT_TIMEOUT)
            except (asyncio.TimeoutError, Timeout):
                last_error = f"{ex.name}: timeout"
                continue
            except Exception as e:  # noqa: BLE001 - si prova il prossimo estrattore
                last_error = f"{ex.name}: {e}"
                continue
            if res is not None and not res.empty and ex.name != "generic":
                return res
            if res is not None and ex.name == "generic":
                return res
        return Extraction(metadata={"error": last_error} if last_error else {}, engine="none")

    async def understand(self, path: Path, ex: Extraction, meta: dict[str, Any]) -> dict[str, Any]:
        """Usa il modello testuale per categoria/descrizione/tag; senza AI usa regole e testo."""
        rule = self.rules.classify(path)
        fallback_desc = (ex.description or ex.text.strip().split("\n")[0])[:240]
        base = {"category": rule["category"], "title": path.stem, "description": fallback_desc,
                "tags": [], "date": None, "amount": None, "source": "rules"}
        if not (self.ai and await self.ai.is_available()) or (not ex.text.strip() and not ex.description):
            return base
        useful_meta = {k: v for k, v in meta.items() if k in (
            "Kind", "WhereFroms", "ContentCreationDate", "Authors", "Title", "NumberOfPages",
            "DurationSeconds", "AcquisitionModel", "Latitude", "Longitude")}
        prompt = (f"File name: {path.name}\nType: {meta.get('Kind', path.suffix)}\n"
                  f"Metadata: {useful_meta}\n\n"
                  + (f"Visual description:\n{ex.description[:1200]}\n\n" if ex.description else "")
                  + (f"Extracted text:\n{ex.text[:2500]}" if ex.text.strip() else ""))
        data = None
        if self.index_model and self.index_model != getattr(self.ai, "model", None):
            try:  # modello più leggero per indicizzare (il 14b resta per chat e domande)
                data = await self.ai.generate_json(prompt, system=summary_system(), model=self.index_model)
            except Exception:
                self.index_model = None  # non disponibile: si usa il modello principale
        if data is None:
            try:
                data = await self.ai.generate_json(prompt, system=summary_system())
            except Exception:
                return base
        cat = str(data.get("category", "")).lower()
        tags = data.get("tags") if isinstance(data.get("tags"), list) else []
        return {
            "category": cat if cat in CATEGORIES else rule["category"],
            "title": str(data.get("title") or path.stem)[:150],
            "description": str(data.get("description") or fallback_desc)[:600],
            "tags": [str(t).lower()[:40] for t in tags][:12],
            "date": data.get("date") if data.get("date") not in ("null", "") else None,
            "amount": data.get("amount") if data.get("amount") not in ("null", "") else None,
            "doc_type": str(data.get("doc_type") or "").lower()[:40] or None,
            "source": "ai",
        }

    async def analyze(self, path: Path, force: bool = False, deep: bool = True) -> dict[str, Any]:
        """Analizza un file (o restituisce l'analisi in indice se il file non è cambiato).

        `deep=False` è il passo veloce: testo, OCR ed embedding, niente modello visivo né riassunto
        dell'AI (il file è subito cercabile; la comprensione arriva dopo con `enrich`)."""
        st = path.stat()
        if not force and self.index.fresh(path, st.st_size, st.st_mtime):
            return self.index.get(path)  # type: ignore[return-value]
        return await self._finish(await self._read(path, vision=deep), understand=deep)

    async def _read(self, path: Path, vision: bool = True) -> tuple[Path, os.stat_result, dict[str, Any], Extraction]:
        """Fase 1: metadati ed estrazione (OCR, modello visivo per immagini/scansioni/video)."""
        st = path.stat()
        if st.st_flags & 0x40000000 if hasattr(st, "st_flags") else False:
            raise CloudOnly(path)
        meta = await shell.mdls(path)
        ex = Extraction(engine="metadata-only") if st.st_size > MAX_FILE_BYTES else await self.extract(path, vision)
        meta.update(ex.metadata)
        return path, st, meta, ex

    async def _finish(self, read: tuple[Path, os.stat_result, dict[str, Any], Extraction],
                      understand: bool = True) -> dict[str, Any]:
        """Fase 2: comprensione con il modello di testo, embedding, salvataggio nell'indice."""
        path, st, meta, ex = read
        info = await self.understand(path, ex, meta) if understand else self._quick_info(path, ex)
        meta.update({k: info[k] for k in ("date", "amount", "doc_type") if info.get(k)})
        # da completare con la comprensione dell'AI? (solo se c'è qualcosa da capire)
        meta["enriched"] = bool(understand) or not (ex.text.strip() or path.suffix.lower() in VISUAL_EXT)

        doc = {
            "path": str(path), "name": path.name, "ext": path.suffix.lower(), "size": st.st_size,
            "mtime": st.st_mtime, "kind": meta.get("Kind", ""), "category": info["category"],
            "title": info["title"], "description": info["description"], "tags": info["tags"],
            "text": (ex.text + ("\n\n" + ex.description if ex.description else ""))[:20_000],
            "metadata": meta, "engine": ex.engine + ("+ai" if info["source"] == "ai" else ""),
            "analyzed_at": None, "error": meta.get("error"),
        }
        embedding = None
        if self.ai and await self.ai.can_embed():
            emb_text = " ".join([path.name, info["title"], info["description"], " ".join(info["tags"]),
                                 ex.description[:800], ex.text[:2000]])
            try:
                vecs = await self.ai.embed([emb_text])
                embedding = vecs[0] if vecs else None
            except Exception:
                embedding = None
        self.index.upsert(doc, embedding)
        return self.index.get(path)  # type: ignore[return-value]

    def _quick_info(self, path: Path, ex: Extraction) -> dict[str, Any]:
        """Passo veloce: categoria dalle regole, titolo dal nome, prima riga del testo, date trovate."""
        from .dates import query_dates

        rule = self.rules.classify(path)
        first = next((ln.strip() for ln in ex.text.splitlines() if len(ln.strip()) > 3), "")
        specs = query_dates(path.stem + " " + ex.text[:1500])
        day = next((s.start.isoformat() for s in specs if s.kind == "day"), None)
        return {"category": rule["category"], "title": path.stem, "description": (ex.description or first)[:240],
                "tags": [], "date": day, "amount": None, "source": "fast"}

    def pending_enrichment(self, root: Path | None = None, limit: int | None = None) -> list[Path]:
        """File indicizzati col passo veloce che aspettano la comprensione dell'AI (i più recenti prima)."""
        return [Path(p) for p in self.index.needs_enrichment(root, limit)]

    async def enrich(self, root: Path | None = None, limit: int | None = None,
                     progress: Callable[..., None] | None = None, cancel: threading.Event | None = None,
                     concurrency: int = 2) -> "AnalyzeStats":
        """Seconda fase: riassunto dell'AI e lettura visiva per i file già cercabili."""
        paths = [p for p in self.pending_enrichment(root, limit) if p.exists()]
        return await self.analyze_many(paths, force=True, progress=progress, cancel=cancel,
                                       concurrency=concurrency, deep=True, phase="enrich")

    async def _analyze_in_phases(self, todo: list[Path], stats: "AnalyzeStats",
                                 progress: Callable[..., None] | None, cancel: threading.Event | None,
                                 concurrency: int) -> None:
        sem = asyncio.Semaphore(concurrency)
        done = 0

        def stop() -> bool:
            return cancel is not None and cancel.is_set()

        async def guarded(fn, p, *args):
            async with sem:
                if stop():
                    return None
                try:
                    return await fn(*args)
                except Exception as e:  # noqa: BLE001
                    stats.errors.append(f"{p.name}: {e}")
                    return None

        for i in range(0, len(todo), PHASE_BATCH):
            if stop():
                return
            batch = todo[i:i + PHASE_BATCH]
            visual = [p for p in batch if p.suffix.lower() in VISUAL_EXT]
            if visual:
                await self.ai.release(TEXT)          # 1) spazio al modello visivo
            if progress:
                progress("analyze", done, len(todo), _("lettura di {n} file", n=len(batch)))
            reads = await asyncio.gather(*(guarded(self._read, p, p) for p in batch))
            if visual:
                await self.ai.release(VISION)        # 2) spazio al modello di testo
            for p, r in zip(batch, reads):
                if r is None or stop():
                    continue
                if progress:
                    progress("analyze", done, len(todo), p.name)
                doc = await guarded(self._finish, p, r)
                if doc is not None:
                    stats.analyzed += 1
                    eng = doc.get("engine", "?")
                    stats.engines[eng] = stats.engines.get(eng, 0) + 1
                done += 1
                if progress:
                    progress("analyze", done, len(todo), p.name)

    async def analyze_many(
        self, paths: list[Path], limit: int | None = None, force: bool = False,
        progress: Callable[..., None] | None = None, cancel: threading.Event | None = None,
        concurrency: int = 2, deep: bool = True, phase: str = "analyze",
    ) -> AnalyzeStats:
        stats = AnalyzeStats()
        start = time.monotonic()
        todo: list[Path] = []
        if not deep:
            concurrency = max(concurrency, 4)  # niente AI generativa: si può parallelizzare di più
        for p in paths:
            try:
                st = p.lstat()
            except OSError:
                continue
            if not is_content(p):
                stats.skipped_noncontent += 1
            elif getattr(st, "st_flags", 0) & 0x40000000:
                stats.skipped_cloud += 1
            elif not force and self.index.fresh(p, st.st_size, st.st_mtime):
                stats.skipped_fresh += 1
            else:
                todo.append(p)
        if limit is not None:
            todo = todo[:limit]

        sem = asyncio.Semaphore(concurrency)
        done = 0
        if progress:
            progress(phase, 0, len(todo))

        # Poca memoria e motori diversi per testo e immagini (es. llama.cpp + Ollama): si lavora a
        # blocchi, prima tutte le letture visive del blocco (modello di testo scaricato), poi tutta
        # la comprensione (modello visivo scaricato): mai i due modelli grandi insieme in memoria.
        swap = getattr(self.ai, "swap_text_and_vision", None)
        if deep and self.ai and self.use_vision and todo and callable(swap) and swap() \
                and await self.ai.can_see():
            await self._analyze_in_phases(todo, stats, progress, cancel, concurrency)
            self.index.flush(force=True)
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            stats.seconds = time.monotonic() - start
            return stats

        async def one(p: Path) -> None:
            nonlocal done
            if cancel is not None and cancel.is_set():
                return
            async with sem:
                if cancel is not None and cancel.is_set():
                    return
                if progress:
                    progress(phase, done, len(todo), p.name)  # file in lavorazione
                try:
                    doc = await self.analyze(p, force=True, deep=deep)
                    stats.analyzed += 1
                    eng = (doc or {}).get("engine", "?")
                    stats.engines[eng] = stats.engines.get(eng, 0) + 1
                except Exception as e:  # noqa: BLE001
                    stats.errors.append(f"{p.name}: {e}")
            done += 1
            if progress:
                progress(phase, done, len(todo), p.name)

        await asyncio.gather(*(one(p) for p in todo))
        self.index.flush(force=True)
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        stats.seconds = time.monotonic() - start
        return stats

    # ------------------------------------------------------------------ ricerca
    async def search(self, query: str, root: Path | None = None, limit: int = 20,
                     names: list[Path] | None = None, precise: bool = True) -> list[Hit]:
        """Ricerca ibrida con reciprocal rank fusion.

        `precise` (comando "cerca"): il testo deve contenere quasi tutte le parole cercate e il
        significato deve superare una soglia alta; se niente è davvero pertinente → nessun
        risultato. Le domande ("chiedi") usano precise=False: più fonti, le filtra il modello."""
        K = 60
        scores: dict[str, float] = {}
        snippets: dict[str, str] = {}
        sources: dict[str, list[str]] = {}

        def add(ranked: list[str], label: str, weight: float = 1.0) -> None:
            for rank, p in enumerate(ranked):
                scores[p] = scores.get(p, 0) + weight / (K + rank + 1)
                sources.setdefault(p, []).append(label)

        # date citate ("31/03/2024", "marzo 2024"): contano come filtro, non come parole qualsiasi
        from .dates import query_dates, strip_dates

        date_specs = query_dates(query)
        text_query = strip_dates(query, date_specs) if date_specs else query
        dated: set[str] = set()
        for spec in date_specs:
            dated.update(self.index.dated(spec, root))
        if date_specs:
            add(sorted(dated), N_("data"), 1.6)

        need = required_terms(len(query_terms(text_query))) if precise else 1
        fts = self.index.fts(text_query, root, 50, min_match=need) if query_terms(text_query) else []
        add([p for p, _s, _n in fts], N_("testo"), 1.0)
        for p, _score, snip in fts:
            snippets.setdefault(p, snip)

        if self.ai and await self.ai.can_embed() and self.index.stats(root)["with_embedding"]:
            try:
                expanded = await self.expand_query(query)
                texts = [query] + ([query + ". " + ", ".join(expanded)] if expanded else [])
                vecs = await self.ai.embed(texts)
                # per ogni documento il punteggio migliore tra richiesta originale ed espansa:
                # i sinonimi aiutano ("macchina" → auto) ma a volte diluiscono ("mediazione")
                best: dict[str, float] = {}
                for vec in vecs:
                    for p, sc in self.index.semantic(vec, root, 50, min_score=0.0):
                        best[p] = max(best.get(p, 0.0), sc)
                sem = sorted(best.items(), key=lambda kv: -kv[1])
                if sem:
                    # soglia assoluta + relativa al migliore: evita risultati "vagamente simili"
                    floor = SEMANTIC_MIN if precise else SEMANTIC_MIN_LOOSE
                    cut = max(floor, sem[0][1] - SEMANTIC_MARGIN)
                    add([p for p, sc in sem if sc >= cut], N_("significato"), 1.2)
            except Exception:
                pass

        spot_query = " ".join(query_terms(text_query)) if precise else query
        spot = await shell.mdfind(spot_query, root, 50) if spot_query else []
        add([str(p) for p in spot if p.is_file()], N_("spotlight"), 0.8)

        if names:
            terms = query_terms(text_query)
            if terms:
                import unicodedata

                def norm(s: str) -> str:
                    s = unicodedata.normalize("NFKD", s.lower())
                    return "".join(c for c in s if not unicodedata.combining(c))

                matched = [str(p) for p in names if all(t in norm(p.name) for t in terms)]
                add(matched[:50], N_("nome"), 1.1)

        if date_specs and dated and precise:
            # è stata chiesta una data precisa e qualcosa corrisponde: solo quei documenti…
            scores = {p: sc for p, sc in scores.items() if p in dated}
            # …e, se alcuni corrispondono anche alle parole cercate, solo quelli
            both = {p for p in scores if len(set(sources.get(p, []))) > 1}
            if both and query_terms(text_query):
                scores = {p: sc for p, sc in scores.items() if p in both}
        ranked = sorted(scores.items(), key=lambda kv: -kv[1])[:limit]
        return [Hit(p, s, self.index.get(p), snippets.get(p, ""), sources.get(p, [])) for p, s in ranked]

    async def expand_query(self, query: str) -> list[str]:
        """Sinonimi e termini collegati generati dall'AI (cache in memoria)."""
        key = query.strip().lower()
        if key in self._expansions:
            return self._expansions[key]
        words: list[str] = []
        if self.ai and await self.ai.is_available():
            try:
                data = await self.ai.generate_json(query, system=expand_system())
                kw = data.get("keywords")
                words = [str(w) for w in kw][:10] if isinstance(kw, list) else []
            except Exception:
                words = []
        self._expansions[key] = words
        return words

    async def ask(self, question: str, root: Path | None = None, names: list[Path] | None = None
                  ) -> tuple[str, list[Hit]]:
        """Risponde a una domanda usando i documenti più pertinenti (RAG locale)."""
        hits = [h for h in await self.search(question, root, 8, names, precise=False) if h.doc]
        if not hits:
            return "", []
        if not (self.ai and await self.ai.is_available()):
            return "", hits
        context = []
        for i, h in enumerate(hits[:6], 1):
            d = h.doc or {}
            context.append(f"[{i}] {d.get('name')} — {d.get('description', '')}\n"
                           f"Metadata: {({k: v for k, v in (d.get('metadata') or {}).items() if k in ('date', 'amount', 'WhereFroms', 'ContentCreationDate')})}\n"
                           f"Content: {(d.get('text') or '')[:1200]}")
        messages = [
            {"role": "system", "content": (
                f"Answer in {language_name()} using ONLY the documents provided (they may be in any "
                "language). Cite sources as [n]. If the documents do not contain the answer, say so "
                "clearly. Be concise.")},
            {"role": "user", "content": "Documents:\n\n" + "\n\n".join(context) + f"\n\nQuestion: {question}"},
        ]
        try:
            answer = await self.ai.chat(messages)
        except Exception:
            answer = ""
        return answer, hits[:6]


_TEMP_DIRS: list[Path] = []


def cleanup_temp() -> None:
    """Rimuove le cartelle temporanee (immagini convertite, pagine renderizzate)."""
    import shutil

    for d in _TEMP_DIRS:
        shutil.rmtree(d, ignore_errors=True)
    _TEMP_DIRS.clear()


atexit.register(cleanup_temp)


class CloudOnly(Exception):
    def __init__(self, path: Path) -> None:
        super().__init__(_("{name} è solo su iCloud: scaricalo per analizzarlo", name=path.name))
