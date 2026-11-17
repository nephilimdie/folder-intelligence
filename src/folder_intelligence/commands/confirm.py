from pathlib import Path

from .. import organizer
from ..intents import YES, Intent
from .base import Command, CommandResult, Request


class ConfirmCommand(Command):
    name = "confirm"
    group = "sistema"
    description = "Conferma l'operazione in attesa"
    priority = 12
    hidden = True
    destructive = True

    def match(self, req: Request):
        return {} if req.norm.rstrip("!. ") in YES else None

    async def run(self, ctx, intent: Intent) -> str:
        if not ctx.pending:
            return "Non c'è nessuna operazione in attesa di conferma."
        plan, ctx.pending = ctx.pending, None
        from ..assistant import PendingAction

        if isinstance(plan, PendingAction):
            return await plan.run()
        ctx.status(f"Eseguo {len(plan)} spostamenti …")
        result = await ctx.blocking(organizer.apply_plan, plan, ctx.source)
        for m in result.done:
            ctx.intelligence.index.move(m.src, m.dst)
        ctx.invalidate()
        verb = "spostati nel Cestino" if plan.kind == "dedupe" else "organizzati"
        lines = [f"✅ {len(result.done)} file {verb}."]
        if result.failed:
            lines.append(f"⚠️  {len(result.failed)} non riusciti:")
            lines += [f"  {Path(m.src).name}: {err}" for m, err in result.failed[:10]]
        await ctx.memory.save(plan.kind, f"{plan.kind} su {plan.root}: {len(result.done)} file {verb}",
                              {"operation_id": result.operation_id})
        if result.operation_id:
            lines.append(f"Registrata nell'audit come operazione {result.operation_id}. "
                         "Per ripristinare: 'annulla l'ultima operazione' (storico: 'mostra lo storico').")
        return CommandResult("\n".join(lines), {
            "operation_id": result.operation_id, "moved": [{"src": m.src, "dst": m.dst} for m in result.done],
            "failed": [{"src": m.src, "error": err} for m, err in result.failed]})
