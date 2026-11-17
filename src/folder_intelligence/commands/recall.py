from ..intents import Intent
from .base import Command, CommandResult


class RecallCommand(Command):
    name = "recall"
    group = "memoria"
    needs_folder = False
    description = "Mostra ciò che ricordo (note e operazioni passate)"
    examples = ("cosa ricordi?",)
    keywords = ("cosa ricordi", "memoria", "ricordi", "cosa abbiamo fatto")
    priority = 22
    params = ("query",)

    def match(self, req):
        return {"query": req.text, "path": None} if req.has(*self.keywords) else None

    async def run(self, ctx, intent: Intent) -> str:
        query = str(intent.args.get("query", ""))
        items = await ctx.memory.query(query) if query else []
        if not items:
            items = await ctx.memory.recent(8)
        if not items:
            return "La memoria è vuota."
        lines = ["Ecco cosa ricordo:"]
        for it in items:
            when = it.get("created_at", "")[:16].replace("T", " ")
            lines.append(f"  • [{it.get('kind', '?')}] {it.get('text', '')}" + (f"  ({when})" if when else ""))
        return CommandResult("\n".join(lines), {"items": items})
