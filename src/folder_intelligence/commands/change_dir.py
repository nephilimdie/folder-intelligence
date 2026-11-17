from ..i18n import N_, _
from ..intents import Intent
from .base import Command, CommandResult


class ChangeDirCommand(Command):
    name = "change_dir"
    group = "sistema"
    description = N_("Cambia la cartella di lavoro")
    examples = (N_("vai in ~/Documents"),)
    keywords = ("vai in", "vai nella", "cambia cartella", "spostati", "apri la cartella", "usa la cartella", "cd ",
                "go to", "change folder", "change directory", "switch to", "open the folder", "use the folder")
    priority = 25

    async def run(self, ctx, intent: Intent) -> str:
        if intent.path is None:
            return _("Cartella attuale: {dir}. Dimmi dove andare, es. 'vai in ~/Documents'.", dir=ctx.directory)
        p = ctx.resolve(intent.path)
        if not p.is_dir():
            return _("La cartella {path} non esiste.", path=p)
        ctx.directory = p
        ctx.pending = None
        return CommandResult(_("Ok, ora lavoro su {path}.", path=p), {"directory": str(p)})
