from ..i18n import N_, _
from ..intents import Intent
from .base import Command, Request


class ExitCommand(Command):
    name = "exit"
    group = "sistema"
    needs_folder = False
    description = N_("Chiude la chat")
    examples = (N_("esci"),)
    priority = 10

    def match(self, req: Request):
        return {} if req.norm in {"exit", "quit", "esci", "ciao ciao", "basta", "chiudi", "q", "bye", "goodbye", "close"} else None

    async def run(self, ctx, intent: Intent) -> str:
        return _("A presto! 👋")
