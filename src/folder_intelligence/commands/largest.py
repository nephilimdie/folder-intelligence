from ..intents import Intent
from ..models import format_size
from .base import Command, CommandResult, Request


class LargestCommand(Command):
    name = "largest"
    group = "analisi"
    description = "Elenca i file più grandi (istantaneo: non legge i file)"
    examples = ("mostrami i 10 file più grandi",)
    keywords = ("piu grandi", "piu pesanti", "grossi", "ingombranti", "occupano piu")
    priority = 35
    params = ("n",)

    def match(self, req: Request):
        if not req.has(*self.keywords):
            return None
        m = req.search(r"\b(\d{1,3})\b")
        return {"n": int(m.group(1)) if m else 10}

    async def run(self, ctx, intent: Intent) -> str:
        scan = await ctx.get_scan(intent.path)
        n = max(1, min(int(intent.args.get("n", 10)), 100))
        top = sorted(scan.files, key=lambda f: -f.size_bytes)[:n]
        if not top:
            return "Nessun file trovato."
        lines = [f"I {len(top)} file più grandi in {scan.root}:"]
        lines += [f"  {format_size(f.size_bytes):>9}  {self.rel(f.path, scan.root)}" + ("  ☁️" if f.cloud_only else "")
                  for f in top]
        if any(f.cloud_only for f in top):
            lines.append("\n☁️ = solo su iCloud (non scaricato su questo Mac)")
        return CommandResult("\n".join(lines), {"root": str(scan.root), "items": [
            {"path": str(f.path), "size_bytes": f.size_bytes, "modified": f.modified_iso,
             "cloud_only": f.cloud_only, "category": f.category} for f in top]})
