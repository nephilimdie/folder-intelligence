import re

from ..intents import Intent
from .base import Command, Request


class RememberCommand(Command):
    name = "remember"
    description = "Salva una nota nella memoria persistente"
    examples = ("ricordami di sistemare le foto",)
    keywords = ("ricordami", "ricorda che", "ricordati che", "prendi nota", "annota")
    priority = 20
    params = ("text",)

    def match(self, req: Request):
        if not req.has(*self.keywords):
            return None
        note = re.sub(r"^.*?(ricordami( di| che)?|ricorda(ti)? che|prendi nota( che| di)?|annota)\s*",
                      "", req.text, count=1, flags=re.I).strip()
        return {"text": note or req.text, "path": None}

    async def run(self, ctx, intent: Intent) -> str:
        note = str(intent.args.get("text", "")).strip()
        if not note:
            return "Cosa devo ricordare?"
        await ctx.memory.save("note", note, {"directory": str(ctx.directory)})
        return f"📝 Ok, me lo ricordo: \"{note}\""
