"""Servizio in background: completa la comprensione dei file quando non usi il Mac.

Gira come LaunchAgent di macOS (`folder-intelligence --install-background`), con priorità bassa
(QoS "background": core efficienti, disco rallentato). Lavora solo se:
  - il Mac è collegato all'alimentazione,
  - non tocchi mouse e tastiera da almeno FI_IDLE_MINUTES minuti (default 5),
  - la chat e il server API non sono aperti (il lock dell'indice è libero).
Appena una condizione cade finisce il file in corso, salva l'indice cifrato e si mette in pausa,
liberando la memoria dei modelli. Al prossimo avvio la chat dice cosa ha completato.

Nei log scrive solo conteggi, mai nomi o contenuti dei file.
"""

from __future__ import annotations

import asyncio
import json
import os
import plistlib
import re
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from . import crypto
from .audit import app_home
from .locks import IndexLock, app_wants_index

LABEL = "com.folderintelligence.background"
PURPOSE = "background"
CHECK_SECONDS = 60
BATCH = 8


def idle_minutes_required() -> float:
    try:
        return float(os.environ.get("FI_IDLE_MINUTES", "5"))
    except ValueError:
        return 5.0


# ------------------------------------------------------------------ condizioni
def on_ac_power() -> bool:
    try:
        out = subprocess.run(["pmset", "-g", "batt"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return True  # non verificabile (es. Mac desktop senza pmset utile): si assume alimentato
    return "AC Power" in out or "Battery Power" not in out


def idle_seconds() -> float:
    """Secondi dall'ultimo uso di mouse/tastiera (HIDIdleTime, in nanosecondi)."""
    try:
        out = subprocess.run(["ioreg", "-c", "IOHIDSystem", "-d", "4"], capture_output=True, text=True,
                             timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return 0.0
    m = re.search(r'"HIDIdleTime"\s*=\s*(\d+)', out)
    return int(m.group(1)) / 1e9 if m else 0.0


def can_work() -> tuple[bool, str]:
    if app_wants_index():
        return False, "app"
    if not on_ac_power():
        return False, "battery"
    if idle_seconds() < idle_minutes_required() * 60:
        return False, "user-active"
    return True, ""


# ------------------------------------------------------------------ resoconto
def _status_path() -> Path:
    return app_home() / "background.enc"


def _load_status() -> dict:
    try:
        return json.loads(crypto.read_encrypted(_status_path(), PURPOSE))
    except Exception:
        return {"unreported": 0, "total": 0}


def _record(n: int) -> None:
    st = _load_status()
    st["unreported"] = st.get("unreported", 0) + n
    st["total"] = st.get("total", 0) + n
    st["last_run"] = datetime.now().isoformat(timespec="seconds")
    crypto.write_encrypted(_status_path(), json.dumps(st).encode(), PURPOSE)


def take_report() -> int:
    """Quanti file ha completato il servizio dall'ultima volta che l'hai aperta (e azzera)."""
    st = _load_status()
    n = int(st.get("unreported", 0))
    if n:
        st["unreported"] = 0
        try:
            crypto.write_encrypted(_status_path(), json.dumps(st).encode(), PURPOSE)
        except Exception:
            pass
    return n


# ------------------------------------------------------------------ servizio
def _log(msg: str) -> None:
    print(f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}", flush=True)


async def run_service(once: bool = False, ai=None) -> int:
    """Ciclo del servizio. `once=True`: un solo blocco (test e prove manuali)."""
    from .ai import create_ai
    from .index import Index
    from .intelligence import DocumentIntelligence
    from .threads import Cancelled

    paused_reason = None
    while True:
        ok, why = can_work()
        if not ok:
            if ai is not None and why != paused_reason and hasattr(ai, "close"):
                ai.close()  # libera la memoria dei modelli mentre usi il Mac
            if why != paused_reason:
                _log(f"pausa ({why})")
                paused_reason = why
            if once:
                return 0
            await asyncio.sleep(CHECK_SECONDS)
            continue
        lock = IndexLock()
        if not lock.acquire(blocking=False):
            if once:
                return 0
            await asyncio.sleep(CHECK_SECONDS)
            continue
        paused_reason = None
        try:
            ai = ai or create_ai()
            index = Index()  # ricaricato dal disco: contiene il lavoro della chat
            di = DocumentIntelligence(ai, index=index)
            pending = [p for p in di.pending_enrichment(limit=BATCH) if p.exists()]
            if not pending:
                _log("niente da completare")
                index.flush(force=True)
            else:
                cancel = threading.Event()
                watcher = threading.Thread(target=_watch, args=(cancel,), daemon=True)
                watcher.start()
                try:
                    stats = await di.analyze_many(pending, force=True, deep=True, cancel=cancel, phase="enrich")
                    done = stats.analyzed
                except Cancelled:
                    done = len(pending) - len(di.pending_enrichment(limit=BATCH))
                finally:
                    cancel.set()
                    index.flush(force=True)
                if done > 0:
                    _record(done)
                _log(f"completati {done} file, {len(di.pending_enrichment())} ancora in attesa")
            del di, index
        finally:
            lock.release()
        if once:
            return 0
        await asyncio.sleep(CHECK_SECONDS if pending else CHECK_SECONDS * 10)


def _watch(cancel: threading.Event) -> None:
    """Ferma il blocco appena torni al Mac, stacchi l'alimentazione o apri la chat."""
    while not cancel.is_set():
        ok, _why = can_work()
        if not ok:
            cancel.set()
            return
        time.sleep(2)


# ------------------------------------------------------------------ LaunchAgent
def plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def plist_content() -> dict:
    log = str(app_home() / "background.log")
    env = {k: v for k, v in os.environ.items() if k.startswith("FI_") and k not in ("FI_MASTER_KEY",)}
    return {
        "Label": LABEL,
        # taskpolicy -b: QoS "background" (core efficienti, I/O a bassa priorità)
        "ProgramArguments": ["/usr/sbin/taskpolicy", "-b", sys.executable, "-m", "folder_intelligence",
                             "--background"],
        "RunAtLoad": True,
        "KeepAlive": True,
        "ProcessType": "Background",
        "LowPriorityIO": True,
        "Nice": 10,
        "StandardOutPath": log,
        "StandardErrorPath": log,
        "EnvironmentVariables": env,
    }


def _launchctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True, timeout=20)


def install() -> str:
    path = plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    domain = f"gui/{os.getuid()}"
    _launchctl("bootout", f"{domain}/{LABEL}")  # se già installato, si ricarica
    with open(path, "wb") as f:
        plistlib.dump(plist_content(), f)
    r = _launchctl("bootstrap", domain, str(path))
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or f"launchctl exit {r.returncode}")
    return str(path)


def uninstall() -> bool:
    _launchctl("bootout", f"gui/{os.getuid()}/{LABEL}")
    if plist_path().exists():
        plist_path().unlink()
        return True
    return False


def status() -> dict:
    r = _launchctl("print", f"gui/{os.getuid()}/{LABEL}")
    st = _load_status()
    return {"installed": plist_path().exists(), "running": r.returncode == 0 and "state = running" in r.stdout,
            "loaded": r.returncode == 0, "total_completed": st.get("total", 0), "last_run": st.get("last_run")}
