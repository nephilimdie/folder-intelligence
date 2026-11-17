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
from .index import Index, query_terms
from .threads import Cancelled, Timeout

EXTRACT_TIMEOUT = 240  # secondi massimi per file (OCR + modello visivo inclusi)
MAX_FILE_BYTES = 2 * 1024 ** 3  # oltre: solo metadati
SEMANTIC_MIN = 0.40     # similarità minima (bge-m3) per considerare un risultato
SEMANTIC_MARGIN = 0.07  # distanza massima dal risultato migliore

EXPAND_SYSTEM = ("Espandi la richiesta di ricerca file dell'utente. Rispondi SOLO JSON: "
                 '{"keywords": ["6-10 parole chiave in italiano: sinonimi e termini collegati che '
                 'potrebbero comparire nei documenti cercati"]}')

SUMMARY_SYSTEM = (
    "Sei un archivista. Ricevi il contenuto estratto da un file (testo, OCR, descrizione visiva, "
    "metadati). Rispondi SOLO con JSON valido:\n"
    '{"category": "<una tra: ' + ", ".join(CATEGORIES) + '>", '
    '"title": "<titolo breve in italiano>", '
    '"description": "<1-2 frasi in italiano: cos\'è e di cosa parla>", '
    '"doc_type": "<tipo generico in 1-2 parole: ricevuta, fattura, contratto, polizza, verbale, lettera, cv, '
    'estratto conto, foto, screenshot, presentazione, codice, …>", '
    '"tags": ["<5-10 parole chiave in italiano, minuscole: tipo documento, persone/aziende, luoghi, argomenti, anno>"], '
    '"date": "<data principale AAAA-MM-GG o null>", "amount": "<importo principale con valuta o null>"}'
)


@dataclass
class AnalyzeStats:
    analyzed: int = 0
    skipped_fresh: int = 0
    skipped_cloud: int = 0
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

    # --------------------------------------------------------------- estrazione
    async def extract(self, path: Path) -> Extraction:
        ctx = ExtractContext(ai=self.ai, use_vision=self.use_vision, tmpdir=self._tmp)
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
        prompt = (f"Nome file: {path.name}\nTipo: {meta.get('Kind', path.suffix)}\n"
                  f"Metadati: {useful_meta}\n\n"
                  + (f"Descrizione visiva:\n{ex.description[:1500]}\n\n" if ex.description else "")
                  + (f"Testo estratto:\n{ex.text[:3500]}" if ex.text.strip() else ""))
        try:
            data = await self.ai.generate_json(prompt, system=SUMMARY_SYSTEM)
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

    async def analyze(self, path: Path, force: bool = False) -> dict[str, Any]:
        """Analizza un file (o restituisce l'analisi in indice se il file non è cambiato)."""
        st = path.stat()
        if not force and self.index.fresh(path, st.st_size, st.st_mtime):
            return self.index.get(path)  # type: ignore[return-value]
        if st.st_flags & 0x40000000 if hasattr(st, "st_flags") else False:
            raise CloudOnly(path)

        meta = await shell.mdls(path)
        ex = Extraction(engine="metadata-only") if st.st_size > MAX_FILE_BYTES else await self.extract(path)
        meta.update(ex.metadata)
        info = await self.understand(path, ex, meta)
        meta.update({k: info[k] for k in ("date", "amount", "doc_type") if info.get(k)})

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

    async def analyze_many(
        self, paths: list[Path], limit: int | None = None, force: bool = False,
        progress: Callable[..., None] | None = None, cancel: threading.Event | None = None,
        concurrency: int = 2,
    ) -> AnalyzeStats:
        stats = AnalyzeStats()
        start = time.monotonic()
        todo: list[Path] = []
        for p in paths:
            try:
                st = p.lstat()
            except OSError:
                continue
            if getattr(st, "st_flags", 0) & 0x40000000:
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
            progress("analyze", 0, len(todo))

        async def one(p: Path) -> None:
            nonlocal done
            if cancel is not None and cancel.is_set():
                return
            async with sem:
                if cancel is not None and cancel.is_set():
                    return
                if progress:
                    progress("analyze", done, len(todo), p.name)  # file in lavorazione
                try:
                    doc = await self.analyze(p, force=True)
                    stats.analyzed += 1
                    eng = (doc or {}).get("engine", "?")
                    stats.engines[eng] = stats.engines.get(eng, 0) + 1
                except Exception as e:  # noqa: BLE001
                    stats.errors.append(f"{p.name}: {e}")
            done += 1
            if progress:
                progress("analyze", done, len(todo), p.name)

        await asyncio.gather(*(one(p) for p in todo))
        self.index.flush(force=True)
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        stats.seconds = time.monotonic() - start
        return stats

    # ------------------------------------------------------------------ ricerca
    async def search(self, query: str, root: Path | None = None, limit: int = 20,
                     names: list[Path] | None = None) -> list[Hit]:
        """Ricerca ibrida con reciprocal rank fusion."""
        K = 60
        scores: dict[str, float] = {}
        snippets: dict[str, str] = {}
        sources: dict[str, list[str]] = {}

        def add(ranked: list[str], label: str, weight: float = 1.0) -> None:
            for rank, p in enumerate(ranked):
                scores[p] = scores.get(p, 0) + weight / (K + rank + 1)
                sources.setdefault(p, []).append(label)

        fts = self.index.fts(query, root, 50)
        add([p for p, _, _ in fts], "testo", 1.0)
        for p, _, snip in fts:
            snippets.setdefault(p, snip)

        if self.ai and await self.ai.can_embed() and self.index.stats(root)["with_embedding"]:
            try:
                expanded = await self.expand_query(query)
                vec = (await self.ai.embed([query + (". " + ", ".join(expanded) if expanded else "")]))[0]
                sem = self.index.semantic(vec, root, 50, min_score=0.0)
                if sem:
                    # soglia assoluta + relativa al migliore: evita risultati "vagamente simili"
                    cut = max(SEMANTIC_MIN, sem[0][1] - SEMANTIC_MARGIN)
                    add([p for p, sc in sem if sc >= cut], "significato", 1.2)
            except Exception:
                pass

        spot = await shell.mdfind(query, root, 50)
        add([str(p) for p in spot if p.is_file()], "spotlight", 0.8)

        if names:
            terms = query_terms(query)
            if terms:
                import unicodedata

                def norm(s: str) -> str:
                    s = unicodedata.normalize("NFKD", s.lower())
                    return "".join(c for c in s if not unicodedata.combining(c))

                matched = [str(p) for p in names if all(t in norm(p.name) for t in terms)]
                add(matched[:50], "nome", 1.1)

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
                data = await self.ai.generate_json(query, system=EXPAND_SYSTEM)
                kw = data.get("keywords")
                words = [str(w) for w in kw][:10] if isinstance(kw, list) else []
            except Exception:
                words = []
        self._expansions[key] = words
        return words

    async def ask(self, question: str, root: Path | None = None, names: list[Path] | None = None
                  ) -> tuple[str, list[Hit]]:
        """Risponde a una domanda usando i documenti più pertinenti (RAG locale)."""
        hits = [h for h in await self.search(question, root, 8, names) if h.doc]
        if not hits:
            return "", []
        if not (self.ai and await self.ai.is_available()):
            return "", hits
        context = []
        for i, h in enumerate(hits[:6], 1):
            d = h.doc or {}
            context.append(f"[{i}] {d.get('name')} — {d.get('description', '')}\n"
                           f"Metadati: {({k: v for k, v in (d.get('metadata') or {}).items() if k in ('date', 'amount', 'WhereFroms', 'ContentCreationDate')})}\n"
                           f"Contenuto: {(d.get('text') or '')[:1200]}")
        messages = [
            {"role": "system", "content": (
                "Rispondi in italiano usando SOLO i documenti forniti. Cita le fonti come [n]. "
                "Se i documenti non contengono la risposta dillo chiaramente. Sii conciso.")},
            {"role": "user", "content": "Documenti:\n\n" + "\n\n".join(context) + f"\n\nDomanda: {question}"},
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
        super().__init__(f"{path.name} è solo su iCloud: scaricalo per analizzarlo")
