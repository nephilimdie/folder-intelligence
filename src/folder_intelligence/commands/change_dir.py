from ..intents import Intent
from .base import Command, CommandResult


class ChangeDirCommand(Command):
    name = "change_dir"
    group = "sistema"
    description = "Cambia la cartella di lavoro"
    examples = ("vai in ~/Documents",)
    keywords = ("vai in", "vai nella", "cambia cartella", "spostati", "apri la cartella", "usa la cartella", "cd ")
    priority = 25

    async def run(self, ctx, intent: Intent) -> str:
        if intent.path is None:
            return f"Cartella attuale: {ctx.directory}. Dimmi dove andare, es. 'vai in ~/Documents'."
        p = ctx.resolve(intent.path)
        if not p.is_dir():
            return f"La cartella {p} non esiste."
        ctx.directory = p
        ctx.pending = None
        return CommandResult(f"Ok, ora lavoro su {p}.", {"directory": str(p)})
