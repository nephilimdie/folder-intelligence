from pathlib import Path

from .. import audit, organizer
from ..intents import Intent
from .base import Command, CommandResult, Request


class UndoCommand(Command):
    name = "undo"
    description = "Ripristina l'ultima operazione o una specifica dello storico"
    examples = ("annulla l'ultima operazione", "annulla operazione 2")
    keywords = ("annulla l'ultima", "annulla ultima", "undo", "torna indietro", "ripristina")
    priority = 23
    params = ("ref",)
    destructive = True

    def match(self, req: Request):
        m = req.search(r"\b(?:annulla|ripristina|undo)\s+(?:l'?\s*)?(?:operazione|op)\s*#?\s*([0-9a-f]{1,12})\b")
        if m:
            return {"ref": m.group(1), "path": None}
        return {"path": None} if req.has(*self.keywords) else None

    async def run(self, ctx, intent: Intent) -> str:
        ctx.pending = None
        ref = intent.args.get("ref")
        if ref:
            op = audit.find_operation(ref)
            if op is None:
                return f"Nessuna operazione '{ref}' nello storico. Scrivi 'mostra lo storico'."
            if not op.undoable:
                return f"L'operazione {op.id} è già stata annullata."
            result = await ctx.blocking(organizer.undo_operation, op.id, ctx.source)
        else:
            result = await ctx.blocking(organizer.undo_last, ctx.source)
        if result is None:
            return "Non ci sono operazioni da annullare."
        for r in result.restored:
            ctx.intelligence.index.move(r["from"], r["to"])
        ctx.invalidate()
        msg = (f"↩️  Operazione {result.operation_id}: ripristinati {len(result.restored)} file "
               "nella posizione originale.")
        for w in result.warnings[:10]:
            msg += f"\n  ⚠️  {w}"
        if result.failed:
            msg += f"\n⚠️  {len(result.failed)} non ripristinabili:"
            msg += "".join(f"\n  {Path(f['from']).name}: {f['error']}" for f in result.failed[:10])
        await ctx.memory.save("undo", msg)
        return CommandResult(msg, {"operation_id": result.operation_id, "undo_id": result.undo_id,
                                   "restored": result.restored, "failed": result.failed,
                                   "warnings": result.warnings})
