from ..i18n import N_, _
from ..intents import Intent
from .base import Command


class UnknownCommand(Command):
    name = "unknown"
    group = "sistema"
    description = N_("Frase non riconosciuta")
    priority = 999
    hidden = True

    def match(self, req):
        return None

    async def run(self, ctx, intent: Intent) -> str:
        if ctx.pending:
            return _("Ho un'operazione in attesa: rispondi 'sì' per eseguirla o 'no' per annullarla.")
        return _("Non ho capito. Prova con 'trovami i duplicati', 'cerca …', 'organizza i file' oppure 'aiuto'.")
