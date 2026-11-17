import re
from pathlib import Path

from .. import organizer
from ..classifier import CATEGORIES
from ..intents import Intent
from .base import Command, CommandResult, Request


class OrganizeCommand(Command):
    name = "organize"
    group = "organizzazione"
    description = "Sposta i file in cartelle per categoria (con conferma e audit)"
    examples = ("organizza i file", "organizza in ~/Archivio")
    keywords = ("organizza", "riordina", "sistema", "metti in ordine", "smista", "sposta")
    priority = 32
    params = ("target",)
    destructive = True

    def match(self, req: Request):
        if not req.has(*self.keywords):
            return None
        # "organizza in ~/X" → destinazione esplicita
        if (req.paths and len(req.paths) == 1 and re.search(r"\b(in|dentro|verso)\s+[\"'~/.]", req.text)
                and not re.search(r"\b(cartella|folder)\b", req.norm)):
            return {"path": None, "target": req.paths[0]}
        return {}

    async def run(self, ctx, intent: Intent) -> str:
        scan = await ctx.get_scan(intent.path)
        await ctx.get_duplicates(scan)
        await ctx.ensure_classified(scan)
        target = ctx.resolve(intent.target) if intent.target else scan.root
        extras = {f.path for g in scan.duplicates for f in g.extras}
        files = [f for f in scan.files if f.path not in extras]
        plan = organizer.plan_organize(files, scan.root, target)
        if not plan.moves:
            return "I file sono già organizzati. ✅"
        ctx.pending = plan
        counts: dict[str, int] = {}
        for m in plan.moves:
            counts[m.reason] = counts.get(m.reason, 0) + 1
        lines = [f"Piano: sposto {len(plan)} file in {target} suddivisi così:"]
        for cat, n in sorted(counts.items(), key=lambda kv: -kv[1]):
            lines.append(f"  📂 {CATEGORIES.get(cat, cat):<32} {n} file")
        lines.append("\nEsempi:")
        for m in plan.moves[:8]:
            lines.append(f"  {Path(m.src).name}  →  {self.rel(m.dst, target)}")
        if extras:
            lines.append(f"\n({len(extras)} copie duplicate escluse: rimuovile con 'cancella i duplicati'.)")
        lines.append("\nConfermi? (sì/no) — potrai annullare con 'annulla l'ultima operazione'.")
        return CommandResult("\n".join(lines), {"plan": {
            "kind": "organize", "target": str(target), "count": len(plan), "by_category": counts,
            "moves": [{"src": m.src, "dst": m.dst, "category": m.reason} for m in plan.moves]}})
