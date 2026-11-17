from pathlib import Path

from .. import organizer
from ..i18n import N_, _, _n
from ..intents import YES, Intent
from .base import Command, CommandResult, Request


class ConfirmCommand(Command):
    name = "confirm"
    group = "sistema"
    description = N_("Conferma l'operazione in attesa")
    priority = 12
    hidden = True
    destructive = True

    def match(self, req: Request):
        return {} if req.norm.rstrip("!. ") in YES else None

    async def run(self, ctx, intent: Intent) -> str:
        if not ctx.pending:
            return _("Non c'è nessuna operazione in attesa di conferma.")
        plan, ctx.pending = ctx.pending, None
        from ..assistant import PendingAction

        if isinstance(plan, PendingAction):
            return await plan.run()
        ctx.status(_("Eseguo {n} spostamenti …", n=len(plan)))
        result = await ctx.blocking(organizer.apply_plan, plan, ctx.source)
        for m in result.done:
            ctx.intelligence.index.move(m.src, m.dst)
        ctx.invalidate()
        n = len(result.done)
        summary = (_n("{n} file spostato nel Cestino", "{n} file spostati nel Cestino", n) if plan.kind == "dedupe"
                   else _n("{n} file organizzato", "{n} file organizzati", n))
        lines = [f"✅ {summary}."]
        if result.failed:
            lines.append(_("❗ {n} non riusciti:", n=len(result.failed)))
            lines += [f"  {Path(m.src).name}: {err}" for m, err in result.failed[:10]]
        await ctx.memory.save(plan.kind, _("{kind} su {root}: {summary}", kind=plan.kind, root=plan.root,
                                           summary=summary),
                              {"operation_id": result.operation_id})
        if result.operation_id:
            lines.append(_("Registrata nell'audit come operazione {id}. Per ripristinare: "
                           "'annulla l'ultima operazione' (storico: 'mostra lo storico').",
                           id=result.operation_id))
        return CommandResult("\n".join(lines), {
            "operation_id": result.operation_id, "moved": [{"src": m.src, "dst": m.dst} for m in result.done],
            "failed": [{"src": m.src, "error": err} for m, err in result.failed]})
