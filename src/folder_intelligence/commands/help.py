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
        return {} if req.norm.rstrip("?! ") in {"help", "aiuto", "", "comandi", "cosa sai fare",
                                               "cosa puoi fare", "?", "commands", "what can you do",
                                               "what do you do"} else None

    async def run(self, ctx, intent: Intent) -> str:
        return ctx.registry.help_text()
