from .. import exporter
from ..intents import Intent
from .base import Command, CommandResult, Request


class ExportCommand(Command):
    name = "export"
    description = "Salva un report della cartella (md, json, csv)"
    examples = ("esporta il report in csv",)
    keywords = ("esporta", "export", "salva report", "salva il report", "report")
    priority = 24
    params = ("format",)

    def match(self, req: Request):
        if not req.has(*self.keywords):
            return None
        fmt = "json" if "json" in req.words else "csv" if ({"csv", "excel"} & req.words) else "md"
        return {"format": fmt}

    async def run(self, ctx, intent: Intent) -> str:
        scan = await ctx.get_scan(None)
        await ctx.get_duplicates(scan)
        await ctx.ensure_classified(scan)
        out = intent.path if intent.path and intent.path.suffix else None
        path = await ctx.blocking(exporter.export, scan, intent.args.get("format", "md"), out)
        return CommandResult(f"📄 Report salvato in {path}", {"path": str(path)})
