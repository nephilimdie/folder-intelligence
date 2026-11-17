"""Sessione conversazionale: il `ctx` che ogni comando riceve.

Contiene lo stato (cartella corrente, scansioni in cache, operazione in attesa di
conferma) e i servizi (motore AI, document intelligence, memoria, audit). Il
riconoscimento delle frasi e la logica dei singoli comandi stanno in `commands/`.
"""

from __future__ import annotations

import copy
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from . import organizer
from .ai import AIEngine
from .classifier import FileClassifier
from .commands import CommandRegistry, CommandResult, registry
from .i18n import _, is_question
from .intelligence import DocumentIntelligence
from .intents import Intent
from .mcp_memory import McpMemory
from .memory import MemoryStrategy, create_memory
from .models import ScanResult
from .scanner import compute_duplicates, scan_directory
from .threads import Cancelled, run_blocking


@dataclass
class PendingAction:
    """Azione in attesa di un sì/no dell'utente (es. 'vuoi che indicizzi la cartella?')."""

    kind: str
    run: Callable[[], Awaitable[str]]
    description: str = ""

    def __len__(self) -> int:
        return 0

    def __bool__(self) -> bool:  # senza questo len()==0 renderebbe l'azione "falsa"
        return True


INDEX_SECONDS = {"image": 9.0, "pdf": 4.0, "media": 8.0, "other": 1.5}


class Assistant:
    def __init__(
        self,
        directory: str | Path,
        memory: MemoryStrategy | None = None,
        ollama: AIEngine | None = None,
        use_ai: bool = True,
        ai_limit: int = 50,
        status: Callable[[str], None] | None = None,
        source: str = "chat",
        progress: Callable[..., None] | None = None,
        scan_cache: dict[Path, ScanResult] | None = None,
        intelligence: DocumentIntelligence | None = None,
        use_vision: bool = True,
        index_limit: int | None = None,   # None = tutti i file
        commands: CommandRegistry | None = None,
        mcp: "McpMemory | None" = None,
        router_model: str | None = None,
        router_mode: str | None = None,
    ) -> None:
        self.directory = Path(directory).expanduser().resolve()
        self.memory = memory or create_memory("json")
        self.ai: AIEngine | None = ollama if use_ai else None
        self.ollama = self.ai  # compatibilità
        self.use_ai = use_ai
        self.ai_limit = ai_limit
        self.index_limit = index_limit
        self.status = status or (lambda _msg: None)
        self.source = source
        self.progress = progress
        self.classifier = FileClassifier(self.ai)
        self.intelligence = intelligence or DocumentIntelligence(
            self.ai if isinstance(self.ai, AIEngine) else None, use_vision=use_vision)
        self.registry = commands or registry()
        self.scans: dict[Path, ScanResult] = scan_cache if scan_cache is not None else {}
        self.pending: organizer.Plan | PendingAction | None = None
        self.history: list[dict[str, str]] = []
        self.cancel = threading.Event()
        self.mcp = mcp if mcp is not None else McpMemory()
        from .router import Router

        self.router = Router(self.registry, self.ai if use_ai else None, model=router_model,
                             mode=router_mode if use_ai else "rules")
        self.plan_rest: list[Intent] = []   # passi del piano che attendono una risposta sì/no

    # ------------------------------------------------------------- servizi ctx
    def resolve(self, path: Path | None) -> Path:
        if path is None:
            return self.directory
        p = path.expanduser()
        if not p.is_absolute():
            p = (self.directory / p) if (self.directory / p).exists() else (Path.cwd() / p)
        return p.resolve()

    _resolve = resolve  # compatibilità

    def blocking(self, fn: Callable[..., Any], *args: Any, timeout: float | None = None):
        """Esegue I/O bloccante in un thread daemon (mai bloccare la chat)."""
        return run_blocking(fn, *args, timeout=timeout)

    async def get_scan(self, path: Path | None = None, refresh: bool = False) -> ScanResult:
        """Scansione veloce (solo metadati), in cache per cartella."""
        root = self.resolve(path)
        if refresh or root not in self.scans:
            self.status(_("Scansiono {root} …", root=root))
            scan = await run_blocking(scan_directory, root, False, self.progress, self.cancel)
            for f in scan.files:
                rule = self.classifier.rules.classify(f.path)
                f.category, f.description = rule["category"], rule["description"]
                f.confidence, f.source = rule["confidence"], rule["source"]
            self.scans[root] = scan
        return self.scans[root]

    async def get_duplicates(self, scan: ScanResult) -> ScanResult:
        """Calcola i duplicati solo quando servono (legge i file: può richiedere tempo)."""
        if not scan.duplicates_checked:
            self.status(_("Confronto i contenuti per trovare i duplicati …"))
            await run_blocking(compute_duplicates, scan, self.progress, self.cancel)
        return scan

    async def ensure_classified(self, scan: ScanResult) -> None:
        """Categorie: prima dall'indice dei contenuti, poi (se serve) AI sul testo."""
        if scan.classified:
            return
        indexed = self.intelligence.index.under(scan.root)
        todo = []
        for f in scan.files:
            doc = indexed.get(str(f.path))
            if doc and doc.get("category"):
                f.category = doc["category"]
                f.description = doc.get("description") or f.description
                f.confidence, f.source = 0.9, "index"
            else:
                todo.append(f)
        if todo and self.use_ai and self.ai and await self.ai.is_available():
            self.status(_("Classifico con AI locale ({model}) …", model=self.ai.model))
            await self.classifier.classify_all(
                [f for f in todo if not f.cloud_only], use_ai=True, ai_limit=self.ai_limit,
                progress=self.progress)
        scan.classified = True

    async def require_index(self, scan: ScanResult, then: Callable[[], Awaitable[str]], why: str) -> str | None:
        """Se la cartella non è indicizzata chiede all'utente se farlo; con 'sì' indicizza e poi esegue `then`.

        Restituisce la domanda da mostrare, oppure None se l'indice c'è già.
        """
        if self.intelligence.index.stats(scan.root)["documents"]:
            return None
        from .intents import Intent

        local = [f for f in scan.files if not f.cloud_only]
        todo = sorted(local, key=lambda f: -f.modified)[: self.index_limit or None]
        if not todo:
            return _("In {root} non ci sono file leggibili su questo Mac (sono tutti solo su iCloud).", root=scan.root)

        async def go() -> str:
            first = await self.dispatch(Intent("index", path=scan.root))
            return first + "\n\n" + await then()

        self.pending = PendingAction("index", go, _("indicizzare {root}", root=scan.root))
        vision = bool(self.ai and self.intelligence.use_vision)
        secs = 0.0
        for f in todo:
            ext = f.extension
            kind = ("image" if ext in {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp", ".tif", ".tiff", ".gif"}
                    else "pdf" if ext == ".pdf" else "media" if ext in {".mp4", ".mov", ".m4v", ".mkv", ".avi"}
                    else "other")
            secs += INDEX_SECONDS[kind] if (vision or kind in ("pdf", "other")) else 1.0
        secs /= 2  # due analisi in parallelo
        eta = (_("circa {n} ore", n=round(secs / 3600, 1)) if secs >= 5400
               else _("circa {n} minuti", n=max(1, round(secs / 60))) if secs >= 90
               else _("meno di un minuto e mezzo"))
        of_total = _(" (su {total})", total=len(local)) if len(local) > len(todo) else ""
        lines = [_("Per {why} devo prima leggere il contenuto dei file di {root}.", why=why, root=scan.root),
                 _("Analizzerei i {n} file più recenti{of_total}: {eta}. Tutto resta sul Mac e l'indice è cifrato.",
                   n=len(todo), of_total=of_total, eta=eta)]
        if scan.cloud_only_count:
            lines.append(_("☁️  {n} file solo su iCloud verranno saltati.", n=scan.cloud_only_count))
        lines.append("\n" + _("Vuoi che li legga ora? (sì/no)"))
        return "\n".join(lines)

    async def sync_mcp(self, root: Path) -> str:
        """Registra la comprensione della cartella nella memoria MCP (secondo la modalità)."""
        if self.mcp.mode == "off":
            return ""
        index = self.intelligence.index
        docs = [d for d in (index.get(p) for p in index.under(root)) if d]
        res = await self.mcp.sync_folder(root, docs)
        if res["error"]:
            return _("⚠️  Memoria MCP: {error} (i dati restano comunque nell'indice locale cifrato).",
                     error=res["error"])
        what = (_("inventario senza contenuti") if res["mode"] == "sealed"
                else _("{n} voci con riassunti ripuliti dai dati personali", n=res["written"]))
        return _("🔐 Memoria MCP aggiornata ({what}, namespace {ns}).", what=what, ns=self.mcp.namespace)

    def skipped_note(self, scan: ScanResult) -> str:
        unread = [e for e in scan.errors if "timeout" in e]
        notes = []
        if scan.cloud_only_count:
            notes.append(_("☁️  {n} file solo su iCloud non controllati", n=scan.cloud_only_count))
        if unread:
            notes.append(_("⏳ {n} file non leggibili (bloccati dalla sincronizzazione), saltati", n=len(unread)))
        return ("\n" + "\n".join(notes)) if notes else ""

    def invalidate(self) -> None:
        self.scans.clear()

    def fork(self, progress: Callable[..., None] | None = None,
             status: Callable[[str], None] | None = None) -> "Assistant":
        """Copia della sessione per un'attività in background: conferma, annullamento e
        avanzamento propri; cache delle scansioni, indice, AI e memoria condivisi."""
        child = copy.copy(self)
        child.pending = None
        child.plan_rest = []
        child.cancel = threading.Event()
        # cronologia condivisa: il router capisce i riferimenti ("e in Downloads?")
        child.progress = progress
        if status is not None:
            child.status = status
        return child

    # --------------------------------------------------------------- dispatch
    async def handle(self, text: str) -> CommandResult:
        """Interpreta ed esegue una frase: il router (AI + regole) produce un piano di 1-4
        comandi, eseguiti in ordine. Restituisce testo (+ `.data` strutturati)."""
        self.cancel.clear()
        plan = await self.router.route(text, self)
        first = plan.steps[0].type if plan.steps else "chat"
        # una domanda sì/no vale solo per la risposta immediatamente successiva
        if self.pending is not None and isinstance(self.pending, PendingAction) \
                and first not in ("confirm", "cancel", "unknown"):
            self.pending = None
        if first not in ("confirm", "cancel"):
            self.plan_rest = []
        self.history.append({"role": "user", "content": text})

        if not plan.steps:
            reply = CommandResult(plan.reply, {"command": "chat"})
        else:
            rest = self.plan_rest if first == "confirm" else []
            if first == "cancel":
                self.plan_rest = []
            reply = await self.run_plan(plan.steps)
            if rest and self.pending is None and not reply.data.get("cancelled"):
                # la risposta "sì" ha sbloccato il piano: proseguiamo con i passi rimasti
                self.plan_rest = []
                more = await self.run_plan(rest, offset=1)
                reply = CommandResult(f"{reply}\n\n{more}",
                                      {**more.data, "before": reply.data})
        reply.data["route"] = plan.to_dict()
        self.history.append({"role": "assistant", "content": reply})
        return reply

    async def run_plan(self, steps: list[Intent], offset: int = 0) -> CommandResult:
        """Esegue i passi in ordine nello stesso contesto. Si ferma se un passo fallisce, viene
        interrotto o chiede una conferma (i passi restanti ripartono dopo il 'sì')."""
        from .router import describe_step

        if len(steps) == 1 and not offset:
            return await self.run_intent(steps[0])
        labels = [describe_step(s, self.registry) for s in steps]
        out: list[str] = []
        if not offset:
            out.append(_("🧭 Farò, in ordine:") + "\n" + "\n".join(f"  {i}. {lbl}" for i, lbl in enumerate(labels, 1)))
        results: list[dict[str, Any]] = []
        for i, step in enumerate(steps):
            if self.cancel.is_set():
                break
            n = f"{i + 1}/{len(steps)}" if not offset else "→"
            self.status(_("Passo {n}: {label} …", n=n, label=labels[i]))
            res = await self.run_intent(step)
            out.append(f"── {n} · {labels[i]}\n{res}")
            results.append(res.data)
            if res.data.get("cancelled") or res.data.get("error"):
                if i < len(steps) - 1:
                    out.append(_("Mi fermo qui: i passi successivi non sono stati eseguiti."))
                break
            if self.pending is not None and i < len(steps) - 1:
                self.plan_rest = steps[i + 1:]
                note = _("Dopo la tua risposta proseguo con: {steps}.", steps=", ".join(labels[i + 1:]))
                # la domanda (sì/no) deve restare l'ultima riga: la nota va subito prima
                lines = out[-1].splitlines()
                q = max((k for k, line in enumerate(lines) if is_question(line)), default=len(lines))
                out[-1] = "\n".join(lines[:q] + [note] + lines[q:])
                break
        data: dict[str, Any] = {"command": "plan", "steps": results}
        if results:
            data["last"] = results[-1]
            if results[-1].get("cancelled"):
                data["cancelled"] = True
        return CommandResult("\n\n".join(out), data)

    async def run_intent(self, intent: Intent) -> CommandResult:
        """Esegue un comando già interpretato (usato anche dalle API), con errori gestiti."""
        self.cancel.clear()
        try:
            reply = await self.dispatch(intent)
        except Cancelled:
            reply = CommandResult(_("⏹️  Interrotto. Nessun file è stato modificato."), {"cancelled": True})
        except NotADirectoryError as e:
            reply = CommandResult(str(e), {"error": "not_a_directory"})
        except PermissionError as e:
            reply = CommandResult(
                _("Permesso negato: {what}. Su macOS concedi 'Accesso completo al disco' al Terminale "
                  "(Impostazioni → Privacy e sicurezza).", what=e.filename or e), {"error": "permission_denied"})
        return reply

    def globally_indexed(self, root: Path) -> bool:
        """True se `root` non è indicizzata ma altre cartelle sì (ricerca/domande su tutto l'indice)."""
        index = self.intelligence.index
        return not index.stats(root)["documents"] and bool(index.stats()["documents"])

    async def dispatch(self, intent: Intent, text: str = "") -> CommandResult:
        cmd = self.registry.get(intent.type) or self.registry.get("unknown")
        # "indicizza Documents" e poi "cerca …": la cartella indicata diventa quella di lavoro
        if intent.path is not None and cmd is not None and cmd.needs_folder:
            folder = self.resolve(intent.path)
            if folder.is_dir():
                self.directory = folder
        res = await cmd.run(self, intent)  # type: ignore[union-attr]
        if not isinstance(res, CommandResult):
            res = CommandResult(str(res))
        res.data.setdefault("command", cmd.name)  # type: ignore[union-attr]
        return res

    def __getattr__(self, name: str):
        """Compatibilità: `assistant.do_scan(intent)` → comando `scan`."""
        if name.startswith("do_"):
            cmd_name = name[3:]

            async def call(intent: Intent | None = None) -> str:
                intent = intent or Intent(cmd_name)
                if intent.type != cmd_name:
                    intent = Intent(cmd_name, intent.path, intent.target, intent.args)
                return await self.dispatch(intent)

            return call
        raise AttributeError(name)
