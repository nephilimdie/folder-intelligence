from ..i18n import N_
from ..intents import Intent
from .base import Command, Request


class HelpCommand(Command):
    name = "help"
    group = "sistema"
    needs_folder = False
    description = N_("Mostra i comandi disponibili")
    examples = (N_("aiuto"),)
    priority = 11

    def match(self, req: Request):
        norm = req.norm.rstrip("?! ")
        if norm in {"aiuto completo", "tutti i comandi", "help all", "all commands", "full help"}:
            return {"full": True}
        return {} if norm in {"help", "aiuto", "", "comandi", "cosa sai fare",
                              "cosa puoi fare", "?", "commands", "what can you do",
                              "what do you do"} else None

    async def run(self, ctx, intent: Intent) -> str:
        if intent.args.get("full"):
            return ctx.registry.help_text()
        return ctx.registry.quick_help()
