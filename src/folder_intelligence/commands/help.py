from ..intents import Intent
from .base import Command, Request


class HelpCommand(Command):
    name = "help"
    description = "Mostra i comandi disponibili"
    examples = ("aiuto",)
    priority = 11

    def match(self, req: Request):
        return {} if req.norm.rstrip("?! ") in {"help", "aiuto", "", "comandi", "cosa sai fare",
                                               "cosa puoi fare", "?"} else None

    async def run(self, ctx, intent: Intent) -> str:
        return ctx.registry.help_text()
