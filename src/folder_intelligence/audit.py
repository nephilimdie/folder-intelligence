"""Audit interno delle modifiche ai file.

Registro append-only in `~/.folder_intelligence/audit.jsonl`: una riga per evento,
cifrata singolarmente (AES-GCM, vedi crypto.py), mai riscritta. Ogni operazione che sposta file (organize, dedupe) registra
per ogni file origine, destinazione, dimensione, data di modifica e (se noto)
SHA-256. Gli annullamenti sono a loro volta eventi che puntano all'operazione
annullata, così lo storico resta completo e verificabile.

Formato eventi:
  {"event": "operation", "id", "ts", "kind", "source", "root", "moves": [...], "failed": [...]}
  {"event": "undo", "id", "ts", "target", "source", "restored": [...], "failed": [...]}
"""

from __future__ import annotations

import fcntl
import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator


def app_home() -> Path:
    """Cartella dati dell'app, sempre con permessi 0700 (solo l'utente)."""
    home = Path(os.environ.get("FI_HOME", "~/.folder_intelligence")).expanduser()
    try:
        if not home.exists():
            home.mkdir(parents=True, mode=0o700)
        elif home.stat().st_mode & 0o077:
            home.chmod(0o700)
    except OSError:
        pass
    return home


def audit_path() -> Path:
    return app_home() / "audit.jsonl"


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


PURPOSE = "audit"


def _encode(event: dict[str, Any]) -> str:
    from . import crypto

    return crypto.encrypt_text(json.dumps(event, ensure_ascii=False), PURPOSE)


def _decode(line: str) -> dict[str, Any] | None:
    from . import crypto

    try:
        return json.loads(crypto.decrypt_text(line, PURPOSE))
    except (json.JSONDecodeError, crypto.CryptoError, ValueError):
        return None  # riga corrotta o manomessa: la saltiamo


def migrate_plaintext() -> int:
    """Cifra (una sola volta) le righe legacy in chiaro, mantenendone ordine e contenuto."""
    path = audit_path()
    if not path.exists():
        return 0
    lines = path.read_text(encoding="utf-8").splitlines()
    if all(line.startswith("enc:") or not line.strip() for line in lines):
        return 0
    from . import crypto

    out, converted = [], 0
    for line in lines:
        if not line.strip():
            continue
        if line.startswith("enc:"):
            out.append(line)
            continue
        try:
            out.append(_encode(json.loads(line)))
            converted += 1
        except json.JSONDecodeError:
            continue
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
        f.flush()
        os.fsync(f.fileno())
    crypto.secure_delete(path)
    os.replace(tmp, path)
    return converted


def append(event: dict[str, Any]) -> dict[str, Any]:
    """Aggiunge un evento cifrato in modo atomico (lock esclusivo + fsync)."""
    path = audit_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    migrate_plaintext()
    line = _encode(event) + "\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
    return event


def events() -> Iterator[dict[str, Any]]:
    path = audit_path()
    if not path.exists():
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            ev = _decode(line)
            if ev is not None:
                yield ev


@dataclass
class Operation:
    """Vista di un'operazione con il suo stato derivato dagli eventi di undo."""

    id: str
    ts: str
    kind: str
    source: str
    root: str
    moves: list[dict[str, Any]]
    failed: list[dict[str, Any]]
    undos: list[dict[str, Any]] = field(default_factory=list)

    @property
    def restored_count(self) -> int:
        return sum(len(u.get("restored", [])) for u in self.undos)

    @property
    def status(self) -> str:
        if not self.moves:
            return "failed"
        if not self.undos:
            return "partial" if self.failed else "done"
        return "undone" if self.restored_count >= len(self.moves) else "partially_undone"

    @property
    def status_label(self) -> str:
        """Stato leggibile nella lingua dell'utente (`status` resta un codice stabile per le API)."""
        from .i18n import N_, _

        labels = {"failed": N_("fallita"), "partial": N_("parziale"), "done": N_("eseguita"),
                  "undone": N_("annullata"), "partially_undone": N_("annullata in parte")}
        return _(labels.get(self.status, self.status))

    @property
    def undoable(self) -> bool:
        return bool(self.moves) and self.restored_count < len(self.moves)

    def to_dict(self, with_moves: bool = False) -> dict[str, Any]:
        d = {
            "id": self.id, "ts": self.ts, "kind": self.kind, "source": self.source,
            "root": self.root, "status": self.status, "status_label": self.status_label,
            "undoable": self.undoable,
            "moved": len(self.moves), "failed": len(self.failed),
            "restored": self.restored_count,
            "undos": [{"id": u["id"], "ts": u["ts"], "restored": len(u.get("restored", [])),
                       "failed": len(u.get("failed", []))} for u in self.undos],
        }
        if with_moves:
            d["moves"] = self.moves
            d["failed_moves"] = self.failed
        return d


def operations() -> list[Operation]:
    """Tutte le operazioni, dalla più recente alla più vecchia."""
    ops: dict[str, Operation] = {}
    for ev in events():
        if ev.get("event") == "operation":
            ops[ev["id"]] = Operation(ev["id"], ev["ts"], ev["kind"], ev.get("source", ""),
                                      ev.get("root", ""), ev.get("moves", []), ev.get("failed", []))
        elif ev.get("event") == "undo" and ev.get("target") in ops:
            ops[ev["target"]].undos.append(ev)
    return list(reversed(list(ops.values())))


def find_operation(ref: str | int) -> Operation | None:
    """Trova un'operazione per id (anche prefisso) o per posizione (1 = più recente)."""
    ops = operations()
    if isinstance(ref, int) or (isinstance(ref, str) and ref.isdigit() and len(ref) <= 4):
        idx = int(ref) - 1
        return ops[idx] if 0 <= idx < len(ops) else None
    matches = [o for o in ops if o.id.startswith(str(ref))]
    return matches[0] if len(matches) == 1 else None


def file_fingerprint(path: Path, sha256: str = "") -> dict[str, Any]:
    st = path.stat()
    fp: dict[str, Any] = {"size": st.st_size, "mtime": st.st_mtime}
    if sha256:
        fp["sha256"] = sha256
    return fp
