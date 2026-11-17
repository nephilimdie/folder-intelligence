"""Indicizzazione automatica dentro la chat, quando è aperta ma ferma.

Mentre la chat è aperta tiene l'indice cifrato per sé, quindi il servizio in background resta
in pausa. Questo lavoratore fa lo stesso lavoro del servizio dentro la chat, con il suo indice e la
sua AI, e solo quando:
- l'indicizzazione automatica è attiva nelle impostazioni e ci sono cartelle sorvegliate;
- nella chat non c'è nulla in corso e non scrivi da almeno CHAT_IDLE_SECONDS;
- il Mac è alimentato (se richiesto) e non è impegnato in altro.

Appena scrivi un tasto si ferma dopo il file in corso; riparte da solo quando torni inattivo.
"""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path
from typing import Any, Callable

CHAT_IDLE_SECONDS = 60
BATCH = 8
RESCAN_SECONDS = 600       # le cartelle sorvegliate si ricontrollano ogni 10 minuti
POWER_CHECK_SECONDS = 60


class IdleIndexer:
    def __init__(self, assistant: Any, busy: Callable[[], bool], on_change: Callable[[], None] = lambda: None,
                 idle_seconds: float = CHAT_IDLE_SECONDS) -> None:
        self.assistant = assistant
        self.busy = busy                      # True se la chat sta facendo qualcosa (attività, conferme)
        self.on_change = on_change
        self.idle_seconds = idle_seconds
        self.last_activity = time.monotonic()
        self.cancel: threading.Event | None = None
        # stato per il box: phase = off | waiting | paused | scan | enrich | done
        self.state: dict[str, Any] = {"phase": "waiting"}
        self._last_scan = 0.0
        self._power: tuple[float, bool] = (0.0, True)

    # ------------------------------------------------------------------ eventi della chat
    def touch(self) -> None:
        """Hai scritto o inviato qualcosa: il lavoro si ferma dopo il file in corso."""
        self.last_activity = time.monotonic()
        if self.cancel is not None:
            self.cancel.set()

    def _set(self, **state: Any) -> None:
        if state != self.state:
            self.state = state
            self.on_change()

    # ------------------------------------------------------------------ condizioni
    def _on_power(self) -> bool:
        from . import background

        now = time.monotonic()
        if now - self._power[0] > POWER_CHECK_SECONDS:
            self._power = (now, background.on_ac_power())
        return self._power[1]

    def can_work(self) -> tuple[bool, str]:
        from . import settings

        prefs = settings.load()
        if not prefs.background_enabled or not prefs.watched_folders:
            return False, "off"
        if self.busy() or time.monotonic() - self.last_activity < self.idle_seconds:
            return False, "chat-active"
        if prefs.require_ac_power and not self._on_power():
            return False, "battery"
        if not settings.system_is_free():
            return False, "busy"
        return True, ""

    # ------------------------------------------------------------------ lavoro
    async def run_forever(self, interval: float = 3.0) -> None:
        while True:
            await asyncio.sleep(interval)
            try:
                await self.step()
            except asyncio.CancelledError:
                raise
            except Exception:
                self._set(phase="waiting")  # un errore qui non deve mai disturbare la chat
                await asyncio.sleep(30)

    async def step(self) -> int:
        """Un giro: se si può, un blocco di lavoro. Restituisce i file completati."""
        ok, why = self.can_work()
        if not ok:
            if self.state.get("phase") not in ("done",) or why == "off":
                self._set(phase="off" if why == "off" else "paused" if why in ("battery", "busy") else "waiting",
                          reason=why)
            return 0
        from . import background, results, settings
        from .scanner import scan_directory
        from .threads import Cancelled, run_blocking

        di = self.assistant.intelligence
        cancel = threading.Event()
        self.cancel = cancel
        done = 0
        try:
            # 1) cartelle sorvegliate: file nuovi o modificati diventano cercabili (passo veloce)
            if time.monotonic() - self._last_scan > RESCAN_SECONDS:
                new = 0
                for folder in settings.load().watched_folders:
                    root = Path(folder)
                    if cancel.is_set() or not root.is_dir():
                        continue
                    self._set(phase="scan", folder=root.name)
                    scan = await run_blocking(scan_directory, root)
                    stats = await di.analyze_many([f.path for f in scan.files], deep=False, cancel=cancel)
                    di.index.remove_missing(root)
                    new += stats.analyzed
                if cancel.is_set():
                    return 0
                self._last_scan = time.monotonic()
                if new:
                    background._event("scanned", new)
                tagged = await results.refresh_saved(di)
                if tagged:
                    background._event("tagged", tagged)
            # 2) comprensione con l'AI dei file in attesa, un piccolo blocco alla volta
            pending = [p for p in di.pending_enrichment(limit=BATCH) if p.exists()]
            if not pending:
                return await self._reembed(di, cancel)
            remaining = len(di.pending_enrichment())

            def progress(_phase, n, total, name=""):
                self._set(phase="enrich", done=n, total=total, file=str(name), remaining=remaining)

            progress("enrich", 0, len(pending))
            try:
                stats = await di.analyze_many(pending, force=True, deep=True, cancel=cancel, phase="enrich",
                                              progress=progress)
                done = stats.analyzed
            except Cancelled:
                done = len(pending) - len([p for p in pending if str(p) in set(di.index.needs_enrichment())])
            if done > 0:
                background._event("enriched", done)
            di.index.flush(force=True)
            self._set(phase="waiting" if cancel.is_set() else "enrich-next", remaining=remaining - done)
            return done
        except Cancelled:
            return done
        finally:
            self.cancel = None

    async def _reembed(self, di, cancel: threading.Event) -> int:
        """3) vettori mancanti (es. dopo un cambio del modello di ricerca), un blocco alla volta."""
        from .vectors import EmbeddingStore, Reembedder

        store = EmbeddingStore(di.index)
        missing = store.count_missing()
        if not missing:
            self._set(phase="done")
            return 0
        self._set(phase="vectors", remaining=missing)
        done = await Reembedder(store, di.ai).run(cancel=cancel)
        if done:
            di.index.flush(force=True)
        if not done:
            self._set(phase="done")  # niente motore per i vettori: non si insiste
        return done
