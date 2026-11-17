from ..intents import Intent
from ..models import format_size
from .base import Command, CommandResult


class SummaryCommand(Command):
    name = "summary"
    description = "Riepilogo della cartella: file, spazio, categorie"
    examples = ("riepilogo", "quanto spazio occupa?")
    keywords = ("riepilogo", "statistiche", "quanto spazio", "sommario", "panoramica", "stato")
    priority = 36

    async def run(self, ctx, intent: Intent) -> str:
        return summary_text(ctx, await ctx.get_scan(intent.path))


def summary_data(ctx, scan) -> dict:
    return {
        "root": str(scan.root), "files": len(scan.files), "total_bytes": scan.total_bytes,
        "cloud_only_files": scan.cloud_only_count,
        "categories": [{"category": c, "files": len(fs), "bytes": sum(f.size_bytes for f in fs)}
                       for c, fs in scan.by_category().items()],
        "indexed_files": ctx.intelligence.index.stats(scan.root)["documents"],
        "duplicates_checked": scan.duplicates_checked,
        "duplicate_groups": len(scan.duplicates), "wasted_bytes": scan.wasted_bytes,
    }


def summary_text(ctx, scan) -> CommandResult:
    if not scan.files:
        return CommandResult(f"La cartella {scan.root} non contiene file (escludendo quelli nascosti).",
                             summary_data(ctx, scan))
    lines = [f"📁 {scan.root}", f"  {len(scan.files)} file, {format_size(scan.total_bytes)} in totale."]
    cats = scan.by_category()
    lines.append("  Categorie: " + ", ".join(f"{c} {len(fs)}" for c, fs in list(cats.items())[:6]))
    if scan.cloud_only_count:
        lines.append(f"  ☁️  {scan.cloud_only_count} file solo su iCloud (non scaricati).")
    stats = ctx.intelligence.index.stats(scan.root)
    if stats["documents"]:
        lines.append(f"  🧠 {stats['documents']} file già analizzati nel contenuto.")
    if scan.duplicates_checked:
        if scan.duplicates:
            lines.append(f"  ⚠️  {len(scan.duplicates)} gruppi di duplicati: {scan.duplicate_file_count} copie, "
                         f"{format_size(scan.wasted_bytes)} recuperabili.")
        else:
            lines.append("  ✅ Nessun duplicato.")
    lines.append("\nPosso: 'trovami i duplicati', 'mostrami i 10 file più grandi', "
                 "'indicizza la cartella' (per cercare nel contenuto), 'organizza i file'.")
    return CommandResult("\n".join(lines), summary_data(ctx, scan))
