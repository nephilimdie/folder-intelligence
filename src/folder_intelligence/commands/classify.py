from ..i18n import N_, _
from ..intents import Intent
from ..models import format_size
from .base import Command, CommandResult


class ClassifyCommand(Command):
    name = "classify"
    group = "analisi"
    description = N_("Raggruppa i file per categoria")
    examples = (N_("classifica i file"), N_("che file ci sono?"))
    keywords = ("classifica", "categorie", "categorizza", "tipi di file", "che file ci sono", "cosa c'e",
                "classify", "categorize", "categorise", "categories", "file types", "what files are there",
                "what kind of files")
    priority = 34

    async def run(self, ctx, intent: Intent) -> str:
        scan = await ctx.get_scan(intent.path)
        await ctx.ensure_classified(scan)
        if not scan.files:
            return _("Nessun file da classificare.")
        lines = [_("Classificazione di {n} file in {root}:", n=len(scan.files), root=scan.root)]
        for cat, files in scan.by_category().items():
            examples = ", ".join(f.name for f in files[:3])
            more = f" (+{len(files) - 3})" if len(files) > 3 else ""
            size = format_size(sum(f.size_bytes for f in files))
            lines.append(f"  • {_(cat):<18} {len(files):>5} " + _("file") + f"  {size:>9}   " + _("es.") + f" {examples}{more}")
        by_src: dict[str, int] = {}
        for f in scan.files:
            by_src[f.source] = by_src.get(f.source, 0) + 1
        if by_src.get("index"):
            lines.append("\n" + _("🧠 {n} file classificati in base al contenuto (indice).", n=by_src["index"]))
        if by_src.get("ollama"):
            lines.append(_("🤖 {n} file classificati leggendone il testo con l'AI.", n=by_src["ollama"]))
        if not by_src.get("index"):
            lines.append("\n" + _("💡 Per classificare in base al contenuto (anche immagini e PDF): 'indicizza la cartella'."))
        lines.append(_("Vuoi che li organizzi in cartelle per categoria? Scrivi 'organizza i file'."))
        return CommandResult("\n".join(lines), {"root": str(scan.root), "sources": by_src, "categories": [
            {"category": c, "files": [str(f.path) for f in fs], "bytes": sum(f.size_bytes for f in fs)}
            for c, fs in scan.by_category().items()]})
