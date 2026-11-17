from ..intents import Intent
from .base import Command, Request


class ExitCommand(Command):
    name = "exit"
    description = "Chiude la chat"
    examples = ("esci",)
    priority = 10

    def match(self, req: Request):
        return {} if req.norm in {"exit", "quit", "esci", "ciao ciao", "basta", "chiudi", "q"} else None

    async def run(self, ctx, intent: Intent) -> str:
        return "A presto! 👋"
