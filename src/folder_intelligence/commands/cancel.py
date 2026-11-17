from ..intents import NO, Intent
from .base import Command, Request


class CancelCommand(Command):
    name = "cancel"
    group = "sistema"
    description = "Annulla l'operazione in attesa di conferma"
    priority = 13
    hidden = True

    def match(self, req: Request):
        return {} if req.norm.rstrip("!. ") in NO else None

    async def run(self, ctx, intent: Intent) -> str:
        if ctx.pending:
            what = getattr(ctx.pending, "kind", "")
            ctx.pending = None
            if what == "index":
                return "Ok, non leggo i file. Quando vuoi: 'indicizza la cartella'."
            return "Ok, operazione annullata. Non ho toccato nessun file."
        return "Nessuna operazione in attesa. (Per ripristinare l'ultima eseguita: 'annulla l'ultima operazione'.)"
