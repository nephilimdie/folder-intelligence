import re

from ..i18n import N_, _
from ..intents import Intent
from .base import Command, Request


class RememberCommand(Command):
    name = "remember"
    group = "memoria"
    needs_folder = False
    description = N_("Salva una nota nella memoria persistente")
    examples = (N_("ricordami di sistemare le foto"),)
    keywords = ("ricordami", "ricorda che", "ricordati che", "prendi nota", "annota",
                "remind me", "remember that", "take note", "make a note", "note that")
    priority = 20
    params = ("text",)

    def match(self, req: Request):
        if not req.has(*self.keywords):
            return None
        note = re.sub(r"^.*?(ricordami( di| che)?|ricorda(ti)? che|prendi nota( che| di)?|annota|"
                      r"remind me( to| that)?|remember that|take note( that| of)?|make a note( that| of)?|"
                      r"note that)\s*",
                      "", req.text, count=1, flags=re.I).strip()
        return {"text": note or req.text, "path": None}

    async def run(self, ctx, intent: Intent) -> str:
        note = str(intent.args.get("text", "")).strip()
        if not note:
            return _("Cosa devo ricordare?")
        await ctx.memory.save("note", note, {"directory": str(ctx.directory)})
        return _("📝 Ok, me lo ricordo: \"{note}\"", note=note)
