from ..intents import Intent
from ..models import format_size
from .base import Command, CommandResult


class ClassifyCommand(Command):
    name = "classify"
    description = "Raggruppa i file per categoria"
    examples = ("classifica i file", "che file ci sono?")
    keywords = ("classifica", "categorie", "categorizza", "tipi di file", "che file ci sono", "cosa c'e")
    priority = 34

    async def run(self, ctx, intent: Intent) -> str:
        scan = await ctx.get_scan(intent.path)
        await ctx.ensure_classified(scan)
        if not scan.files:
            return "Nessun file da classificare."
        lines = [f"Classificazione di {len(scan.files)} file in {scan.root}:"]
        for cat, files in scan.by_category().items():
            examples = ", ".join(f.name for f in files[:3])
            more = f" (+{len(files) - 3})" if len(files) > 3 else ""
            size = format_size(sum(f.size_bytes for f in files))
            lines.append(f"  • {cat:<18} {len(files):>5} file  {size:>9}   es. {examples}{more}")
        by_src: dict[str, int] = {}
        for f in scan.files:
            by_src[f.source] = by_src.get(f.source, 0) + 1
        if by_src.get("index"):
            lines.append(f"\n🧠 {by_src['index']} file classificati in base al contenuto (indice).")
        if by_src.get("ollama"):
            lines.append(f"🤖 {by_src['ollama']} file classificati leggendone il testo con l'AI.")
        if not by_src.get("index"):
            lines.append("\nℹ️  Per classificare in base al contenuto (anche immagini e PDF): 'indicizza la cartella'.")
        lines.append("Vuoi che li organizzi in cartelle per categoria? Scrivi 'organizza i file'.")
        return CommandResult("\n".join(lines), {"root": str(scan.root), "sources": by_src, "categories": [
            {"category": c, "files": [str(f.path) for f in fs], "bytes": sum(f.size_bytes for f in fs)}
            for c, fs in scan.by_category().items()]})
