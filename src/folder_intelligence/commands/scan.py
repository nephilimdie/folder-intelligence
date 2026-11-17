from ..intents import Intent
from .base import Command, Request
from .summary import summary_text


class ScanCommand(Command):
    name = "scan"
    description = "Analizza una cartella (veloce: solo nomi e dimensioni)"
    examples = ("analizza la cartella Downloads", "analizza ~/Desktop")
    keywords = ("analizza", "scansiona", "scan", "controlla", "guarda", "esamina")
    priority = 50

    def match(self, req: Request):
        if req.has(*self.keywords) or (req.paths and len(req.words) <= 4):
            return {}
        return None

    async def run(self, ctx, intent: Intent) -> str:
        scan = await ctx.get_scan(intent.path, refresh=True)
        if intent.path is not None:
            ctx.directory = scan.root
        await ctx.memory.save("scan", f"Scansione di {scan.root}: {len(scan.files)} file",
                              {"root": str(scan.root)})
        return summary_text(ctx, scan)
