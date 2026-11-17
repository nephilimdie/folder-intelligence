from ..intents import Intent
from .base import Command


class UnknownCommand(Command):
    name = "unknown"
    group = "sistema"
    description = "Frase non riconosciuta"
    priority = 999
    hidden = True

    def match(self, req):
        return None

    async def run(self, ctx, intent: Intent) -> str:
        if ctx.pending:
            return "Ho un'operazione in attesa: rispondi 'sì' per eseguirla o 'no' per annullarla."
        return "Non ho capito. Prova con 'trovami i duplicati', 'cerca …', 'organizza i file' oppure 'aiuto'."
