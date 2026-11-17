from ..i18n import N_, _
from ..intents import Intent
from .base import Command, Request
from .summary import summary_text


class ScanCommand(Command):
    name = "scan"
    group = "analisi"
    description = N_("Analizza una cartella (veloce: solo nomi e dimensioni)")
    examples = (N_("analizza la cartella Downloads"), N_("analizza ~/Desktop"))
    keywords = ("analizza", "scansiona", "scan", "controlla", "guarda", "esamina", "analyze", "analyse",
                "check", "look at", "examine", "inspect")
    priority = 50

    def match(self, req: Request):
        if req.has(*self.keywords) or (req.paths and len(req.words) <= 4):
            return {}
        return None

    async def run(self, ctx, intent: Intent) -> str:
        scan = await ctx.get_scan(intent.path, refresh=True)
        if intent.path is not None:
            ctx.directory = scan.root
        await ctx.memory.save("scan", _("Scansione di {root}: {n} file", root=scan.root, n=len(scan.files)),
                              {"root": str(scan.root)})
        return summary_text(ctx, scan)
