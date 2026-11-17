"""Operazioni sul filesystem: organizzazione, rimozione duplicati, annullamento.

Principi di sicurezza:
- ogni operazione è prima un *piano* (dry-run) e viene eseguita solo su richiesta;
- i duplicati non vengono mai cancellati definitivamente: vanno nel Cestino;
- ogni esecuzione viene registrata nell'audit (vedi audit.py) e può essere annullata.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from . import audit
from .classifier import CATEGORIES
from .i18n import _
from .models import DuplicateGroup, FileInfo


def trash_dir() -> Path:
    return Path(os.environ.get("FI_TRASH", "~/.Trash")).expanduser()


@dataclass
class Move:
    src: str
    dst: str
    reason: str = ""
    sha256: str = ""
    keep: str = ""  # dedupe: l'originale che resta (deve esistere ed essere identico)


@dataclass
class Plan:
    kind: str  # "organize" | "dedupe"
    root: str
    moves: list[Move] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.moves)


@dataclass
class ApplyResult:
    done: list[Move]
    failed: list[tuple[Move, str]]
    operation_id: str | None


@dataclass
class UndoResult:
    operation_id: str
    undo_id: str
    restored: list[dict]
    failed: list[dict]
    warnings: list[str]


def unique_destination(dst: Path, reserved: set[Path] | None = None) -> Path:
    """Restituisce `dst` o una variante `nome (n).ext` che non esiste ancora."""
    reserved = reserved or set()
    if not dst.exists() and dst not in reserved:
        return dst
    stem, suffix = dst.stem, dst.suffix
    n = 1
    while True:
        cand = dst.with_name(f"{stem} ({n}){suffix}")
        if not cand.exists() and cand not in reserved:
            return cand
        n += 1


def category_folder_names(sub: str) -> set[str]:
    """Nome della cartella di categoria in ogni lingua supportata ("Documenti" / "Documents")."""
    from .i18n import SUPPORTED, catalog

    return {sub} | {catalog(code).get(sub, sub) for code in SUPPORTED}


def plan_organize(files: list[FileInfo], root: Path, target: Path | None = None) -> Plan:
    """Pianifica lo spostamento di ogni file in `target/<Cartella categoria>/`."""
    target = (target or root).expanduser().resolve()
    plan = Plan(kind="organize", root=str(root))
    reserved: set[Path] = set()
    # cartelle di categoria in tutte le lingue: un file già organizzato (anche in un'altra lingua) resta dov'è
    category_dirs = {target / name for sub in CATEGORIES.values() for name in category_folder_names(sub)}

    for f in files:
        folder = target / _(CATEGORIES.get(f.category, CATEGORIES["altro"]))
        # già nella cartella giusta (o in una sua sottocartella) → nulla da fare
        if f.path.parent == folder or folder in f.path.parents:
            continue
        # file già dentro un'altra cartella di categoria creata da noi → lascia stare
        if any(d in f.path.parents for d in category_dirs) and target in f.path.parents:
            plan.skipped.append(str(f.path))
            continue
        dst = unique_destination(folder / f.name, reserved)
        reserved.add(dst)
        plan.moves.append(Move(str(f.path), str(dst), f.category))
    return plan


def plan_dedupe(groups: list[DuplicateGroup], root: Path) -> Plan:
    """Pianifica lo spostamento nel Cestino di tutte le copie tranne l'originale."""
    plan = Plan(kind="dedupe", root=str(root))
    trash = trash_dir()
    reserved: set[Path] = set()
    for g in groups:
        for dup in g.extras:
            dst = unique_destination(trash / dup.name, reserved)
            reserved.add(dst)
            plan.moves.append(Move(str(dup.path), str(dst), _("copia di {path}", path=g.keep.path), g.sha256,
                                   keep=str(g.keep.path)))
    return plan


class NotDuplicateAnymore(OSError):
    pass


def _verify_still_duplicate(src: Path, keep: Path, sha256: str) -> None:
    """Prima di cestinare una copia ricontrolla, sul disco di adesso, che l'originale
    esista ancora e che entrambi abbiano ancora lo stesso contenuto (i file potrebbero
    essere stati modificati dal Finder o da altre app dopo la scansione)."""
    from .scanner import hash_file

    if not keep.is_file():
        raise NotDuplicateAnymore(_("l'originale {name} non esiste più: copia lasciata al suo posto", name=keep.name))
    if not sha256 or hash_file(src) != sha256 or hash_file(keep) != sha256:
        raise NotDuplicateAnymore(_("il contenuto è cambiato dopo la scansione: file lasciato al suo posto"))


def apply_plan(plan: Plan, source: str = "cli") -> ApplyResult:
    """Esegue il piano e registra l'operazione nell'audit (anche se fallisce in parte)."""
    done: list[Move] = []
    failed: list[tuple[Move, str]] = []
    audited_moves: list[dict] = []
    audited_failed: list[dict] = []
    for m in plan.moves:
        src, dst = Path(m.src), Path(m.dst)
        try:
            if not src.exists():
                raise FileNotFoundError(_("file non più presente"))
            if m.keep:
                _verify_still_duplicate(src, Path(m.keep), m.sha256)
            fp = audit.file_fingerprint(src, m.sha256)
            dst = unique_destination(dst)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            done.append(Move(str(src), str(dst), m.reason, m.sha256))
            audited_moves.append({"src": str(src), "dst": str(dst), "reason": m.reason, **fp})
        except OSError as e:
            failed.append((m, str(e)))
            audited_failed.append({"src": m.src, "dst": m.dst, "error": str(e)})

    op_id = None
    if plan.moves:
        op_id = audit.new_id()
        audit.append({
            "event": "operation", "id": op_id, "ts": audit.now(), "kind": plan.kind,
            "source": source, "root": plan.root, "moves": audited_moves, "failed": audited_failed,
        })
    return ApplyResult(done, failed, op_id)


def undo_operation(ref: str | int, source: str = "cli") -> UndoResult | None:
    """Annulla un'operazione specifica (id, prefisso id o posizione nello storico).

    Ripristina solo i file non ancora ripristinati; se un file è stato modificato
    dopo lo spostamento viene comunque rimesso al suo posto ma segnalato.
    """
    op = audit.find_operation(ref)
    if op is None:
        return None
    return _undo(op, source)


def undo_last(source: str = "cli") -> UndoResult | None:
    """Annulla l'operazione più recente che abbia ancora file da ripristinare."""
    for op in audit.operations():
        if op.undoable:
            return _undo(op, source)
    return None


def _undo(op: audit.Operation, source: str) -> UndoResult:
    already = {r["from"] for u in op.undos for r in u.get("restored", [])}
    restored: list[dict] = []
    failed: list[dict] = []
    warnings: list[str] = []
    for m in reversed(op.moves):
        if m["dst"] in already:
            continue
        cur, original = Path(m["dst"]), Path(m["src"])
        try:
            if not cur.exists():
                raise FileNotFoundError(_("file non trovato nella destinazione (spostato o Cestino svuotato?)"))
            if cur.stat().st_size != m.get("size", cur.stat().st_size):
                warnings.append(_("{name}: modificato dopo lo spostamento, ripristinato comunque", name=cur.name))
            target = unique_destination(original)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(cur), str(target))
            restored.append({"from": str(cur), "to": str(target)})
            _remove_empty_parents(cur.parent, Path(op.root))
        except OSError as e:
            failed.append({"from": m["dst"], "to": m["src"], "error": str(e)})

    undo_id = audit.new_id()
    audit.append({"event": "undo", "id": undo_id, "ts": audit.now(), "target": op.id,
                  "source": source, "restored": restored, "failed": failed})
    return UndoResult(op.id, undo_id, restored, failed, warnings)


def _remove_empty_parents(folder: Path, stop: Path) -> None:
    """Rimuove cartelle vuote create dall'organizzazione, senza risalire oltre `stop`."""
    try:
        while folder != stop and stop in folder.parents and not any(folder.iterdir()):
            folder.rmdir()
            folder = folder.parent
    except OSError:
        pass
