from pathlib import Path

from .. import audit, organizer
from ..i18n import N_, _
from ..intents import Intent
from .base import Command, CommandResult, Request


class UndoCommand(Command):
    name = "undo"
    group = "organizzazione"
    needs_folder = False
    description = N_("Ripristina l'ultima operazione o una specifica dello storico")
    examples = (N_("annulla l'ultima operazione"), N_("annulla operazione 2"))
    keywords = ("annulla l'ultima", "annulla ultima", "undo", "torna indietro", "ripristina", "revert",
                "restore", "roll back", "rollback", "put everything back")
    priority = 23
    params = ("ref",)
    destructive = True

    def match(self, req: Request):
        m = req.search(r"\b(?:annulla|ripristina|undo|revert|restore)\s+(?:l'?\s*|the\s+)?"
                       r"(?:operazione|operation|op)\s*#?\s*([0-9a-f]{1,12})\b")
        if m:
            return {"ref": m.group(1), "path": None}
        return {"path": None} if req.has(*self.keywords) else None

    async def run(self, ctx, intent: Intent) -> str:
        ctx.pending = None
        ref = intent.args.get("ref")
        if ref:
            op = audit.find_operation(ref)
            if op is None:
                return _("Nessuna operazione '{ref}' nello storico. Scrivi 'mostra lo storico'.", ref=ref)
            if not op.undoable:
                return _("L'operazione {id} è già stata annullata.", id=op.id)
            result = await ctx.blocking(organizer.undo_operation, op.id, ctx.source)
        else:
            result = await ctx.blocking(organizer.undo_last, ctx.source)
        if result is None:
            return _("Non ci sono operazioni da annullare.")
        for r in result.restored:
            ctx.intelligence.index.move(r["from"], r["to"])
        ctx.invalidate()
        msg = _("↩️  Operazione {id}: ripristinati {n} file nella posizione originale.",
                id=result.operation_id, n=len(result.restored))
        for w in result.warnings[:10]:
            msg += f"\n  ⚠️  {w}"
        if result.failed:
            msg += "\n" + _("⚠️  {n} non ripristinabili:", n=len(result.failed))
            msg += "".join(f"\n  {Path(f['from']).name}: {f['error']}" for f in result.failed[:10])
        await ctx.memory.save("undo", msg)
        return CommandResult(msg, {"operation_id": result.operation_id, "undo_id": result.undo_id,
                                   "restored": result.restored, "failed": result.failed,
                                   "warnings": result.warnings})
