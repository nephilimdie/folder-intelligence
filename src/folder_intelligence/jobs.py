"""Attività (job) in background, condivise da chat e API.

Ogni comando viene eseguito come Job: ha un id breve, uno stato, l'avanzamento
(fase, fatti/totale, dettaglio), il risultato (testo + dati strutturati) e, se il
comando chiede conferma (es. spostare file), la domanda in sospeso. La chat mostra
i job nel box laterale; le API li espongono su /jobs.
"""

from __future__ import annotations

import asyncio
import itertools
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

RUNNING, DONE, ERROR, CANCELLED = "running", "done", "error", "cancelled"
KEEP_FINISHED = 50
FORCE_CANCEL_AFTER = 2.0


@dataclass
class Job:
    id: str
    title: str
    command: str
    ctx: Any = None                       # sessione (Assistant) su cui gira il comando
    status: str = RUNNING
    phase: str = ""
    done: int = 0
    total: int | None = None
    detail: str = ""
    message: str = ""                     # ultimo messaggio di stato ("Scansiono …")
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    result_text: str = ""
    result_data: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    confirmation: dict[str, Any] | None = None   # domanda sì/no in sospeso
    background: bool = False               # la chat l'ha spostato in background
    seen: bool = False                     # risultato già mostrato all'utente
    cancel: threading.Event = field(default_factory=threading.Event)
    task: asyncio.Task | None = None
    manager: "JobManager | None" = None

    # ------------------------------------------------------------ avanzamento
    def progress(self, phase: str, done: int, total: int | None, detail: str = "") -> None:
        self.phase, self.done, self.total, self.detail = phase, done, total, detail
        self._changed()

    def set_message(self, msg: str) -> None:
        self.message = msg.strip(" …")
        self._changed()

    def _changed(self) -> None:
        if self.manager:
            self.manager.touch()

    @property
    def elapsed(self) -> float:
        return (self.finished_at or time.time()) - self.started_at

    @property
    def fraction(self) -> float | None:
        return (self.done / self.total) if self.total else None

    @property
    def active(self) -> bool:
        return self.status == RUNNING

    def to_dict(self, with_result: bool = True) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id, "title": self.title, "command": self.command, "status": self.status,
            "progress": {"phase": self.phase, "done": self.done, "total": self.total,
                         "fraction": self.fraction, "detail": self.detail, "message": self.message},
            "started_at": self.started_at, "finished_at": self.finished_at,
            "elapsed_s": round(self.elapsed, 1), "needs_confirmation": self.confirmation,
        }
        if self.error:
            d["error"] = self.error
        if with_result and self.status != RUNNING:
            d["result"] = {"text": self.result_text, "data": self.result_data}
        return d


class JobManager:
    def __init__(self) -> None:
        self.jobs: dict[str, Job] = {}
        self._ids = itertools.count(1)
        self.version = 0
        self._listeners: list[Callable[[], None]] = []

    # ---------------------------------------------------------------- eventi
    def subscribe(self, fn: Callable[[], None]) -> None:
        self._listeners.append(fn)

    def touch(self) -> None:
        self.version += 1
        for fn in list(self._listeners):
            try:
                fn()
            except Exception:
                pass

    async def wait_change(self, version: int, timeout: float = 15.0) -> int:
        """Attende una modifica successiva a `version` (per SSE/polling lungo)."""
        end = time.monotonic() + timeout
        while self.version == version and time.monotonic() < end:
            await asyncio.sleep(0.25)
        return self.version

    # ---------------------------------------------------------------- avvio
    def start(self, title: str, command: str, ctx: Any,
              run: Callable[[Job], Awaitable[Any]]) -> Job:
        job = Job(id=str(next(self._ids)), title=title, command=command, ctx=ctx, manager=self)
        self.jobs[job.id] = job

        async def runner() -> None:
            try:
                result = await run(job)
                job.result_text = str(result or "")
                job.result_data = dict(getattr(result, "data", {}) or {})
                job.status = CANCELLED if (job.cancel.is_set() or job.result_data.get("cancelled")) else DONE
                pending = getattr(ctx, "pending", None)
                if pending is not None:
                    job.confirmation = describe_pending(pending, job.result_text)
            except asyncio.CancelledError:
                job.status = CANCELLED
                job.result_text = "⏹️  Interrotto. Nessun file è stato modificato."
            except Exception as e:  # noqa: BLE001 - riportato all'utente
                job.status = ERROR
                job.error = str(e) or e.__class__.__name__
                job.result_text = f"❌ Errore: {job.error}"
            finally:
                job.finished_at = time.time()
                self._prune()
                self.touch()

        job.task = asyncio.ensure_future(runner())
        self.touch()
        return job

    def get(self, job_id: str) -> Job | None:
        return self.jobs.get(str(job_id))

    def list(self, active_only: bool = False) -> list[Job]:
        jobs = sorted(self.jobs.values(), key=lambda j: -j.started_at)
        return [j for j in jobs if j.active] if active_only else jobs

    def active(self) -> list[Job]:
        return self.list(active_only=True)

    def cancel(self, job_id: str) -> Job | None:
        job = self.get(job_id)
        if job is None or not job.active:
            return job
        job.cancel.set()
        if job.ctx is not None and hasattr(job.ctx, "cancel"):
            job.ctx.cancel.set()
        loop = asyncio.get_running_loop()
        # se l'operazione è bloccata e non risponde al flag, la interrompiamo dopo poco
        loop.call_later(FORCE_CANCEL_AFTER, lambda: job.task and not job.task.done() and job.task.cancel())
        self.touch()
        return job

    def cancel_all(self) -> None:
        for j in self.active():
            self.cancel(j.id)

    def _prune(self) -> None:
        finished = [j for j in self.list() if not j.active and not j.confirmation]
        for j in finished[KEEP_FINISHED:]:
            self.jobs.pop(j.id, None)


def describe_pending(pending: Any, text: str) -> dict[str, Any]:
    """Descrizione serializzabile di una conferma in sospeso."""
    kind = getattr(pending, "kind", "")
    question = "Confermi?"
    for line in reversed(text.splitlines()):
        if "(sì/no)" in line:
            question = line.replace(" (sì/no)", "").strip()
            break
    destructive = kind in ("dedupe", "organize")
    return {"kind": kind, "question": question, "destructive": destructive,
            "count": len(pending) if hasattr(pending, "__len__") else None,
            "default": "no" if destructive else "yes"}

