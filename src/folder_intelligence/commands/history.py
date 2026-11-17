from .. import audit
from ..i18n import N_, _
from ..intents import Intent
from .base import Command, CommandResult


class HistoryCommand(Command):
    name = "history"
    group = "organizzazione"
    needs_folder = False
    description = N_("Audit: storico di tutte le modifiche ai file")
    examples = (N_("mostra lo storico"),)
    keywords = ("storico", "cronologia", "audit", "registro", "operazioni fatte", "operazioni eseguite",
                "cosa hai modificato", "cosa hai spostato", "modifiche fatte", "history", "what did you change",
                "what did you move", "changes made", "operations log")
    priority = 21

    async def run(self, ctx, intent: Intent) -> str:
        ops = audit.operations()
        if not ops:
            return _("Nessuna modifica ai file registrata finora.")
        labels = {"organize": _("organizza"), "dedupe": _("rimuovi duplicati")}
        lines = [_("Storico delle modifiche ai file (audit):")]
        for i, op in enumerate(ops[:15], 1):
            when = op.ts.replace("T", " ")[:16]
            lines.append(f"  [{i}] {when}  {op.id}  {labels.get(op.kind, op.kind):<18} "
                         f"{len(op.moves):>4} file  {op.status_label:<18} {op.root}")
        if len(ops) > 15:
            lines.append(_("  … e altre {n} operazioni", n=len(ops) - 15))
        lines.append("\n" + _("Per ripristinarne una: 'annulla operazione 2' oppure 'annulla operazione <id>'."))
        return CommandResult("\n".join(lines), {"operations": [o.to_dict() for o in ops]})
