"""API HTTP locali per l'app Mac (FastAPI).

Avvio:  folder-intelligence --serve            (http://127.0.0.1:8765, docs su /docs)

Sicurezza:
- ascolta solo su 127.0.0.1 per default;
- ogni endpoint (tranne /health) richiede `Authorization: Bearer <token>`; il token
  è in ~/.folder_intelligence/api_token (permessi 0600) e l'app Mac lo legge da lì;
- le operazioni sui file passano dagli stessi piani/conferme/audit della CLI.
"""

from __future__ import annotations

import asyncio  # noqa: F401
import os
import secrets
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from . import __version__, audit, completion, exporter, organizer
from .ai import AIEngine, create_ai
from .assistant import Assistant
from .audit import app_home
from .classifier import CATEGORIES
from .commands import registry
from .intelligence import CloudOnly, DocumentIntelligence
from .intents import Intent
from .jobs import JobManager
from .threads import Cancelled, run_blocking
from .memory import MemoryStrategy, create_memory
from .models import FileInfo, ScanResult, format_size

MAX_PLANS = 50
MAX_SESSIONS = 50


# ------------------------------------------------------------------ auth token
def token_path() -> Path:
    return app_home() / "api_token"


def get_or_create_token() -> str:
    env = os.environ.get("FI_API_TOKEN")
    if env:
        return env
    path = token_path()
    if path.exists():
        return path.read_text().strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(token)
    return token


# ---------------------------------------------------------------------- schemi
class ScanRequest(BaseModel):
    path: str = Field(..., examples=["~/Downloads"])
    duplicates: bool = Field(False, description="cerca anche i duplicati (legge i file)")
    classify: bool = Field(False, description="classifica anche con l'AI (più lento)")
    index: bool = Field(False, description="analizza il contenuto (OCR, visione, documenti) e indicizza")
    index_limit: int = Field(200, ge=1, le=100_000)
    refresh: bool = Field(True, description="ignora la cache e riscansiona")


class PlanRequest(BaseModel):
    kind: Literal["dedupe", "organize"]
    path: str
    target: str | None = Field(None, description="solo per organize: cartella di destinazione")


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None
    directory: str | None = Field(None, description="cartella di lavoro iniziale della sessione")


class MemoryRequest(BaseModel):
    text: str


class AskRequest(BaseModel):
    question: str
    path: str


class CommandRequest(BaseModel):
    path: str | None = Field(None, description="cartella su cui agire (default: cartella predefinita)")
    target: str | None = Field(None, description="destinazione (es. per organize)")
    args: dict[str, Any] = Field(default_factory=dict, description="argomenti del comando (vedi GET /commands)")
    wait: float = Field(2.0, ge=0, le=60, description="secondi di attesa: se finisce prima la risposta è completa, "
                                                      "altrimenti 202 e il job prosegue in background")


class ConfirmRequest(BaseModel):
    confirm: bool
    wait: float = Field(2.0, ge=0, le=60)


# ----------------------------------------------------------------------- stato
class Job:
    def __init__(self, path: Path, classify: bool) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.path = path
        self.classify = classify
        self.status = "running"  # running | done | error
        self.phase = "scan"
        self.done = 0
        self.total: int | None = None
        self.detail = ""
        self.error = ""
        self.started_at = datetime.now().isoformat(timespec="seconds")
        self.finished_at = ""
        self.result: ScanResult | None = None
        self.cancel = threading.Event()
        self.index_stats: dict[str, Any] | None = None

    def progress(self, phase: str, done: int, total: int | None, detail: str = "") -> None:
        self.phase, self.done, self.total, self.detail = phase, done, total, detail

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id, "path": str(self.path), "status": self.status,
            "progress": {"phase": self.phase, "done": self.done, "total": self.total,
                         "detail": self.detail,
                         "fraction": (self.done / self.total) if self.total else None},
            "started_at": self.started_at, "finished_at": self.finished_at,
        }
        if self.error:
            d["error"] = self.error
        if self.result is not None:
            d["summary"] = summary(self.result)
        if self.index_stats is not None:
            d["index"] = self.index_stats
        return d


class State:
    def __init__(self, ollama: AIEngine | None, use_ai: bool, memory: MemoryStrategy,
                 default_dir: Path, use_vision: bool = True) -> None:
        self.ollama = ollama
        self.intelligence = DocumentIntelligence(ollama, use_vision=use_vision)
        self.use_ai = use_ai
        self.memory = memory
        self.default_dir = default_dir
        self.scans: dict[Path, ScanResult] = {}
        self.jobs: dict[str, Job] = {}
        self.plans: dict[str, tuple[organizer.Plan, str]] = {}  # id -> (piano, creato_il)
        self.sessions: dict[str, Assistant] = {}
        self.fs_lock = asyncio.Lock()
        self.work = JobManager()  # job generici dei comandi (/commands, /jobs)

    def assistant(self, directory: Path | None = None, progress=None) -> Assistant:
        return Assistant(directory or self.default_dir, memory=self.memory, ollama=self.ollama,
                         use_ai=self.use_ai, source="api", scan_cache=self.scans, progress=progress,
                         intelligence=self.intelligence)


# --------------------------------------------------------------------- helpers
def resolve_dir(raw: str) -> Path:
    p = Path(raw).expanduser().resolve()
    if not p.is_dir():
        raise HTTPException(404, f"La cartella {p} non esiste")
    return p


def file_dict(f: FileInfo) -> dict[str, Any]:
    d = f.to_dict()
    d["size_human"] = format_size(f.size_bytes)
    return d


def summary(scan: ScanResult) -> dict[str, Any]:
    cats = scan.by_category()
    return {
        "root": str(scan.root), "scanned_at": scan.scanned_at, "classified": scan.classified,
        "duplicates_checked": scan.duplicates_checked, "cloud_only_files": scan.cloud_only_count,
        "files": len(scan.files), "total_bytes": scan.total_bytes,
        "total_human": format_size(scan.total_bytes),
        "duplicate_groups": len(scan.duplicates), "duplicate_files": scan.duplicate_file_count,
        "wasted_bytes": scan.wasted_bytes, "wasted_human": format_size(scan.wasted_bytes),
        "categories": [{"category": c, "folder": CATEGORIES.get(c, c), "files": len(fs),
                        "bytes": sum(f.size_bytes for f in fs)} for c, fs in cats.items()],
        "errors": len(scan.errors),
    }


def plan_dict(plan_id: str, plan: organizer.Plan, created: str) -> dict[str, Any]:
    return {"id": plan_id, "kind": plan.kind, "root": plan.root, "created_at": created,
            "count": len(plan.moves),
            "moves": [{"src": m.src, "dst": m.dst, "reason": m.reason} for m in plan.moves]}


# ------------------------------------------------------------------------- app
def create_app(
    ollama: AIEngine | None = None,
    use_ai: bool = True,
    memory: MemoryStrategy | None = None,
    default_dir: str | Path = "~/Downloads",
    token: str | None = None,
    use_vision: bool = True,
) -> FastAPI:
    state = State(ollama if use_ai else None, use_ai, memory or create_memory("json"),
                  Path(default_dir).expanduser(), use_vision)
    api_token = token or get_or_create_token()

    app = FastAPI(title="folderIntelligence API", version=__version__,
                  description="API locali per organizzare cartelle, trovare duplicati e "
                              "ripristinare modifiche (audit).")
    app.state.fi = state

    def auth(request: Request) -> None:
        header = request.headers.get("authorization", "")
        given = header[7:] if header.lower().startswith("bearer ") else ""
        if not given or not secrets.compare_digest(given, api_token):
            raise HTTPException(401, "Token mancante o non valido",
                                headers={"WWW-Authenticate": "Bearer"})

    secured = [Depends(auth)]

    @app.exception_handler(PermissionError)
    async def _perm(_req, exc: PermissionError):
        return JSONResponse(status_code=403, content={
            "detail": f"Permesso negato: {exc.filename or exc}. Concedi 'Accesso completo al disco'."})

    async def get_scan(path: str) -> ScanResult:
        root = resolve_dir(path)
        return await state.assistant(root).get_scan(root)

    # --------------------------------------------------------------- generali
    @app.get("/health", tags=["generale"])
    async def health() -> dict[str, Any]:
        return {"ok": True, "version": __version__, "service": "folder-intelligence"}

    @app.get("/status", tags=["generale"], dependencies=secured)
    async def status() -> dict[str, Any]:
        ai = bool(state.ollama and await state.ollama.is_available())
        return {"version": __version__, "ai_available": ai,
                "model": state.ollama.model if state.ollama else None,
                "engines": state.ollama.describe() if state.ollama else None,
                "default_directory": str(state.default_dir),
                "cached_scans": [str(p) for p in state.scans],
                "running_jobs": sum(1 for j in state.jobs.values() if j.status == "running")
                + len(state.work.active())}

    # --------------------------------------------------------------- scansioni
    @app.post("/scans", tags=["scansione"], dependencies=secured, status_code=202)
    async def start_scan(req: ScanRequest) -> dict[str, Any]:
        root = resolve_dir(req.path)
        job = Job(root, req.classify)
        state.jobs[job.id] = job

        async def run() -> None:
            try:
                a = state.assistant(root, progress=job.progress)
                a.cancel = job.cancel
                scan = await a.get_scan(root, refresh=req.refresh)
                if req.duplicates:
                    await a.get_duplicates(scan)
                if req.index:
                    st = await state.intelligence.analyze_many(
                        [f.path for f in sorted(scan.files, key=lambda f: -f.modified)],
                        limit=req.index_limit, progress=job.progress, cancel=job.cancel)
                    job.index_stats = {"analyzed": st.analyzed, "skipped_fresh": st.skipped_fresh,
                                       "skipped_cloud": st.skipped_cloud, "errors": st.errors[:20],
                                       "engines": st.engines, "seconds": round(st.seconds, 1),
                                       "mcp": await a.sync_mcp(scan.root)}
                    scan.classified = False
                if req.classify or req.index:
                    await a.ensure_classified(scan)
                job.result = scan
                job.status = "done"
            except Cancelled:
                job.status = "cancelled"
            except Exception as e:  # noqa: BLE001 - riportato al client
                job.status, job.error = "error", str(e)
            job.finished_at = datetime.now().isoformat(timespec="seconds")

        asyncio.create_task(run())
        return job.to_dict()

    @app.get("/scans/{job_id}", tags=["scansione"], dependencies=secured)
    async def scan_status(job_id: str) -> dict[str, Any]:
        job = state.jobs.get(job_id)
        if not job:
            raise HTTPException(404, "Scansione non trovata")
        return job.to_dict()

    @app.delete("/scans/{job_id}", tags=["scansione"], dependencies=secured, status_code=202)
    async def cancel_scan(job_id: str) -> dict[str, Any]:
        job = state.jobs.get(job_id)
        if not job:
            raise HTTPException(404, "Scansione non trovata")
        job.cancel.set()
        return job.to_dict()

    INTERNAL = {"confirm", "cancel", "unknown", "exit", "help"}

    @app.get("/commands", tags=["comandi"], dependencies=secured)
    async def list_commands() -> dict[str, Any]:
        """Tutti i comandi della chat, ciascuno eseguibile con POST /commands/{name}."""
        return {"items": [{"name": c.name, "description": c.description, "examples": list(c.examples),
                           "destructive": c.destructive, "params": ["path", *c.params],
                           "endpoint": f"/commands/{c.name}"}
                          for c in registry().commands if not c.hidden and c.name not in INTERNAL]}

    async def _job_response(job, wait: float) -> JSONResponse:
        if wait > 0 and job.task is not None:
            await asyncio.wait({job.task}, timeout=wait)
        return JSONResponse(job.to_dict(), status_code=200 if not job.active else 202)

    def _start(ctx: Assistant, title: str, command: str, runner):
        holder: dict[str, Any] = {}
        ctx.progress = lambda *a: holder["job"].progress(*a) if "job" in holder else None
        ctx.status = lambda m: holder["job"].set_message(m) if "job" in holder else None
        job = state.work.start(title, command, ctx, runner)
        holder["job"] = job
        return job

    @app.post("/commands/{name}", tags=["comandi"], dependencies=secured,
              responses={202: {"description": "in esecuzione: segui GET /jobs/{id}"}})
    async def run_command(name: str, req: CommandRequest) -> JSONResponse:
        """Esegue un comando come job. I comandi che modificano file restituiscono
        `needs_confirmation`: conferma con POST /jobs/{id}/confirm."""
        cmd = registry().get(name)
        if cmd is None or cmd.hidden or name in INTERNAL:
            raise HTTPException(404, f"Comando '{name}' inesistente (vedi GET /commands)")
        root = resolve_dir(req.path) if req.path else state.default_dir
        ctx = state.assistant(root)
        intent = Intent(name, path=root if req.path else None,
                        target=Path(req.target).expanduser() if req.target else None, args=dict(req.args))
        title = f"{name} {req.path or ''}".strip()
        job = _start(ctx, title, name, lambda j: ctx.run_intent(intent))
        return await _job_response(job, req.wait)

    @app.get("/jobs", tags=["job"], dependencies=secured)
    async def list_jobs(active: bool = False) -> dict[str, Any]:
        return {"version": state.work.version,
                "items": [j.to_dict(with_result=False) for j in state.work.list(active_only=active)]}

    @app.get("/jobs/events", tags=["job"], dependencies=secured)
    async def job_events(request: Request) -> StreamingResponse:
        """Server-Sent Events: un evento a ogni cambiamento dei job (avanzamento, fine, conferme)."""
        import json as _json

        async def stream():
            version = -1
            while not await request.is_disconnected():
                version = await state.work.wait_change(version, timeout=15)
                payload = {"version": version, "jobs": [j.to_dict(with_result=False) for j in state.work.list()[:30]]}
                yield f"data: {_json.dumps(payload, ensure_ascii=False)}\n\n"
                await asyncio.sleep(0.2)  # al massimo ~5 aggiornamenti al secondo

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache"})

    @app.get("/jobs/{job_id}", tags=["job"], dependencies=secured)
    async def get_job(job_id: str) -> dict[str, Any]:
        job = state.work.get(job_id)
        if job is None:
            raise HTTPException(404, "Job non trovato")
        return job.to_dict()

    @app.delete("/jobs/{job_id}", tags=["job"], dependencies=secured, status_code=202)
    async def cancel_job(job_id: str) -> dict[str, Any]:
        job = state.work.cancel(job_id)
        if job is None:
            raise HTTPException(404, "Job non trovato")
        return job.to_dict(with_result=False)

    @app.post("/jobs/{job_id}/confirm", tags=["job"], dependencies=secured)
    async def confirm_job(job_id: str, req: ConfirmRequest) -> JSONResponse:
        """Risponde sì/no alla conferma richiesta da un job; avvia un nuovo job con l'esito."""
        job = state.work.get(job_id)
        if job is None:
            raise HTTPException(404, "Job non trovato")
        if not job.confirmation or job.ctx is None or not job.ctx.pending:
            raise HTTPException(409, "Questo job non attende conferme")
        job.confirmation = None
        ctx = job.ctx
        answer = Intent("confirm" if req.confirm else "cancel")
        nxt = _start(ctx, f"{'conferma' if req.confirm else 'annulla'} #{job.id}", answer.type,
                     lambda j: ctx.run_intent(answer))
        return await _job_response(nxt, req.wait)

    @app.get("/summary", tags=["cartelle"], dependencies=secured)
    async def get_summary(path: str) -> dict[str, Any]:
        return summary(await get_scan(path))

    @app.get("/files", tags=["cartelle"], dependencies=secured)
    async def list_files(
        path: str, category: str | None = None, q: str | None = None,
        sort: Literal["size", "name", "modified"] = "size", desc: bool = True,
        limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0),
    ) -> dict[str, Any]:
        scan = await get_scan(path)
        files = scan.files
        if category:
            files = [f for f in files if f.category == category]
        if q:
            ql = q.lower()
            files = [f for f in files if ql in f.name.lower() or ql in f.description.lower()]
        key = {"size": lambda f: f.size_bytes, "name": lambda f: f.name.lower(),
               "modified": lambda f: f.modified}[sort]
        files = sorted(files, key=key, reverse=desc)
        return {"total": len(files), "offset": offset,
                "items": [file_dict(f) for f in files[offset:offset + limit]]}

    @app.get("/duplicates", tags=["cartelle"], dependencies=secured)
    async def list_duplicates(path: str, limit: int = Query(100, ge=1, le=1000),
                              offset: int = Query(0, ge=0)) -> dict[str, Any]:
        scan = await get_scan(path)
        await state.assistant(scan.root).get_duplicates(scan)
        groups = scan.duplicates[offset:offset + limit]
        return {"total": len(scan.duplicates), "wasted_bytes": scan.wasted_bytes,
                "items": [{**g.to_dict(), "size_human": format_size(g.size_bytes),
                           "keep": file_dict(g.keep), "duplicates": [file_dict(f) for f in g.extras]}
                          for g in groups]}

    @app.get("/folders", tags=["cartelle"], dependencies=secured)
    async def list_folders(path: str = "~") -> dict[str, Any]:
        root = resolve_dir(path)
        items = []
        try:
            for e in sorted(os.scandir(root), key=lambda e: e.name.lower()):
                if e.name.startswith(".") or not e.is_dir(follow_symlinks=False):
                    continue
                items.append({"name": e.name, "path": str(Path(e.path))})
        except PermissionError as e:
            raise HTTPException(403, f"Permesso negato: {root}") from e
        return {"path": str(root), "parent": str(root.parent), "items": items}

    @app.get("/complete", tags=["cartelle"], dependencies=secured)
    async def complete(text: str, cwd: str | None = None) -> dict[str, Any]:
        base = resolve_dir(cwd) if cwd else state.default_dir
        return {"items": [{"text": c.text, "start": c.start, "display": c.display, "meta": c.meta}
                          for c in completion.complete(text, base)]}

    # ------------------------------------------------------------------- piani
    @app.post("/plans", tags=["modifiche"], dependencies=secured, status_code=201)
    async def create_plan(req: PlanRequest) -> dict[str, Any]:
        root = resolve_dir(req.path)
        a = state.assistant(root)
        scan = await a.get_scan(root)
        await a.get_duplicates(scan)
        if req.kind == "dedupe":
            plan = organizer.plan_dedupe(scan.duplicates, scan.root)
        else:
            await a.ensure_classified(scan)
            extras = {f.path for g in scan.duplicates for f in g.extras}
            target = Path(req.target).expanduser().resolve() if req.target else scan.root
            plan = organizer.plan_organize([f for f in scan.files if f.path not in extras],
                                           scan.root, target)
        plan_id = uuid.uuid4().hex[:12]
        created = datetime.now().isoformat(timespec="seconds")
        state.plans[plan_id] = (plan, created)
        while len(state.plans) > MAX_PLANS:
            state.plans.pop(next(iter(state.plans)))
        return plan_dict(plan_id, plan, created)

    @app.get("/plans/{plan_id}", tags=["modifiche"], dependencies=secured)
    async def get_plan(plan_id: str) -> dict[str, Any]:
        if plan_id not in state.plans:
            raise HTTPException(404, "Piano non trovato o scaduto")
        plan, created = state.plans[plan_id]
        return plan_dict(plan_id, plan, created)

    @app.delete("/plans/{plan_id}", tags=["modifiche"], dependencies=secured, status_code=204)
    async def discard_plan(plan_id: str) -> None:
        state.plans.pop(plan_id, None)

    @app.post("/plans/{plan_id}/apply", tags=["modifiche"], dependencies=secured)
    async def apply(plan_id: str) -> dict[str, Any]:
        entry = state.plans.pop(plan_id, None)
        if entry is None:
            raise HTTPException(404, "Piano non trovato, scaduto o già eseguito")
        plan, _ = entry
        async with state.fs_lock:
            result = await run_blocking(organizer.apply_plan, plan, "api")
        for m in result.done:
            state.intelligence.index.move(m.src, m.dst)
        state.scans.clear()
        return {"operation_id": result.operation_id, "moved": len(result.done),
                "failed": [{"src": m.src, "error": err} for m, err in result.failed]}

    # ------------------------------------------------------------------- audit
    @app.get("/operations", tags=["audit"], dependencies=secured)
    async def list_operations(limit: int = Query(50, ge=1, le=500)) -> dict[str, Any]:
        ops = audit.operations()
        return {"total": len(ops), "items": [o.to_dict() for o in ops[:limit]]}

    @app.get("/operations/{op_id}", tags=["audit"], dependencies=secured)
    async def get_operation(op_id: str) -> dict[str, Any]:
        op = audit.find_operation(op_id)
        if op is None:
            raise HTTPException(404, "Operazione non trovata")
        return op.to_dict(with_moves=True)

    async def _undo(ref: str | None) -> dict[str, Any]:
        async with state.fs_lock:
            if ref is None:
                result = await run_blocking(organizer.undo_last, "api")
            else:
                op = audit.find_operation(ref)
                if op is None:
                    raise HTTPException(404, "Operazione non trovata")
                if not op.undoable:
                    raise HTTPException(409, "Operazione già annullata")
                result = await run_blocking(organizer.undo_operation, op.id, "api")
        if result is None:
            raise HTTPException(404, "Nessuna operazione da annullare")
        for r in result.restored:
            state.intelligence.index.move(r["from"], r["to"])
        state.scans.clear()
        return {"operation_id": result.operation_id, "undo_id": result.undo_id,
                "restored": len(result.restored), "failed": result.failed,
                "warnings": result.warnings}

    @app.post("/operations/{op_id}/undo", tags=["audit"], dependencies=secured)
    async def undo_operation(op_id: str) -> dict[str, Any]:
        return await _undo(op_id)

    @app.post("/undo", tags=["audit"], dependencies=secured)
    async def undo_last() -> dict[str, Any]:
        return await _undo(None)

    # -------------------------------------------------------------------- chat
    @app.post("/chat", tags=["chat"], dependencies=secured)
    async def chat(req: ChatRequest) -> dict[str, Any]:
        sid = req.session_id or uuid.uuid4().hex[:12]
        a = state.sessions.get(sid)
        if a is None:
            start = resolve_dir(req.directory) if req.directory else state.default_dir
            a = state.sessions[sid] = state.assistant(start)
            while len(state.sessions) > MAX_SESSIONS:
                state.sessions.pop(next(iter(state.sessions)))
        async with state.fs_lock:
            reply = await a.handle(req.message)
        pending = None
        if a.pending:
            pending = {"kind": a.pending.kind, "count": len(a.pending),
                       "confirm_with": "sì", "cancel_with": "no"}
        return {"session_id": sid, "reply": reply, "directory": str(a.directory),
                "pending": pending, "data": getattr(reply, "data", {})}

    @app.post("/route", tags=["chat"], dependencies=secured)
    async def route(req: ChatRequest) -> dict[str, Any]:
        """Anteprima: quali comandi (e in che ordine) eseguirebbe la frase, senza eseguirli.
        `source` = ai | rules | shortcut; `reply` = risposta diretta quando non serve nessun comando."""
        start = resolve_dir(req.directory) if req.directory else state.default_dir
        a = state.sessions.get(req.session_id or "") or state.assistant(start)
        plan = await a.router.route(req.message, a)
        return plan.to_dict()

    @app.delete("/chat/{session_id}", tags=["chat"], dependencies=secured, status_code=204)
    async def end_chat(session_id: str) -> None:
        state.sessions.pop(session_id, None)

    # ------------------------------------------------------------------ memoria
    @app.get("/memory", tags=["memoria"], dependencies=secured)
    async def memory_query(q: str | None = None, limit: int = Query(10, ge=1, le=100)) -> dict[str, Any]:
        items = await state.memory.query(q, limit) if q else await state.memory.recent(limit)
        return {"items": items}

    @app.post("/memory", tags=["memoria"], dependencies=secured, status_code=201)
    async def memory_save(req: MemoryRequest) -> dict[str, Any]:
        await state.memory.save("note", req.text, {"source": "api"})
        return {"ok": True}

    # ------------------------------------------------------- document intelligence
    @app.get("/search", tags=["contenuti"], dependencies=secured)
    async def search(q: str, path: str, limit: int = Query(20, ge=1, le=100)) -> dict[str, Any]:
        scan = await get_scan(path)
        hits = await state.intelligence.search(q, scan.root, limit, names=[f.path for f in scan.files])
        return {"query": q, "indexed": state.intelligence.index.stats(scan.root),
                "items": [{"path": h.path, "score": round(h.score, 4), "matched_by": list(dict.fromkeys(h.sources)),
                           "snippet": h.snippet,
                           "doc": {k: (h.doc or {}).get(k) for k in ("name", "title", "description", "category",
                                                                    "tags", "size", "engine")} if h.doc else None}
                          for h in hits]}

    @app.post("/ask", tags=["contenuti"], dependencies=secured)
    async def ask(req: AskRequest) -> dict[str, Any]:
        scan = await get_scan(req.path)
        answer, hits = await state.intelligence.ask(req.question, scan.root, [f.path for f in scan.files])
        return {"answer": answer, "sources": [{"n": i, "path": h.path, "title": (h.doc or {}).get("title")}
                                              for i, h in enumerate(hits, 1)]}

    @app.get("/describe", tags=["contenuti"], dependencies=secured)
    async def describe(path: str, force: bool = False) -> dict[str, Any]:
        p = Path(path).expanduser().resolve()
        if not p.is_file():
            raise HTTPException(404, f"File {p} non trovato")
        try:
            return await state.intelligence.analyze(p, force=force)
        except CloudOnly as e:
            raise HTTPException(409, str(e)) from e

    @app.get("/index/stats", tags=["contenuti"], dependencies=secured)
    async def index_stats(path: str | None = None) -> dict[str, Any]:
        root = resolve_dir(path) if path else None
        return state.intelligence.index.stats(root)

    # ------------------------------------------------------------------ export
    @app.get("/export", tags=["cartelle"], dependencies=secured)
    async def export(path: str, format: Literal["json", "md", "csv"] = "json"):
        scan = await get_scan(path)
        out = app_home() / "exports" / f"report-{scan.root.name or 'root'}.{format}"
        await run_blocking(exporter.export, scan, format, out)
        media = {"json": "application/json", "md": "text/markdown", "csv": "text/csv"}[format]
        return FileResponse(out, media_type=media, filename=out.name)

    return app


def serve(host: str = "127.0.0.1", port: int = 8765, ai: AIEngine | None = None,
          use_ai: bool = True, memory: str = "json", use_vision: bool = True) -> int:
    import uvicorn

    token = get_or_create_token()
    if ai is None and use_ai:
        ai = create_ai()
    app = create_app(ai, ai is not None, create_memory(memory), token=token, use_vision=use_vision)
    if host not in ("127.0.0.1", "localhost", "::1"):
        print(f"⚠️  Attenzione: API esposte su {host}, raggiungibili da altri dispositivi in rete.")
    print(f"🤖 folderIntelligence API su http://{host}:{port}  (documentazione: /docs)")
    print(f"🔑 Token in {token_path()}  →  header 'Authorization: Bearer <token>'")
    uvicorn.run(app, host=host, port=port, log_level="info")
    return 0


__all__ = ["create_app", "serve", "Intent"]
