from .. import audit
from ..intents import Intent
from .base import Command, CommandResult


class HistoryCommand(Command):
    name = "history"
    group = "organizzazione"
    needs_folder = False
    description = "Audit: storico di tutte le modifiche ai file"
    examples = ("mostra lo storico",)
    keywords = ("storico", "cronologia", "audit", "registro", "operazioni fatte", "operazioni eseguite",
                "cosa hai modificato", "cosa hai spostato", "modifiche fatte")
    priority = 21

    async def run(self, ctx, intent: Intent) -> str:
        ops = audit.operations()
        if not ops:
            return "Nessuna modifica ai file registrata finora."
        labels = {"organize": "organizza", "dedupe": "rimuovi duplicati"}
        lines = ["Storico delle modifiche ai file (audit):"]
        for i, op in enumerate(ops[:15], 1):
            when = op.ts.replace("T", " ")[:16]
            lines.append(f"  [{i}] {when}  {op.id}  {labels.get(op.kind, op.kind):<18} "
                         f"{len(op.moves):>4} file  {op.status:<18} {op.root}")
        if len(ops) > 15:
            lines.append(f"  … e altre {len(ops) - 15} operazioni")
        lines.append("\nPer ripristinarne una: 'annulla operazione 2' oppure 'annulla operazione <id>'.")
        return CommandResult("\n".join(lines), {"operations": [o.to_dict() for o in ops]})
