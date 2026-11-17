from ..i18n import N_, _
from ..intents import Intent
from ..models import format_size
from .base import Command, CommandResult


class SummaryCommand(Command):
    name = "summary"
    group = "analisi"
    description = N_("Riepilogo della cartella: file, spazio, categorie")
    examples = (N_("riepilogo"), N_("quanto spazio occupa?"))
    keywords = ("riepilogo", "statistiche", "quanto spazio", "sommario", "panoramica", "stato", "summary",
                "statistics", "stats", "how much space", "status", "overview")
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
        return CommandResult(_("La cartella {root} non contiene file (escludendo quelli nascosti).", root=scan.root),
                             summary_data(ctx, scan))
    lines = [f"📁 {scan.root}", _("  {n} file, {size} in totale.", n=len(scan.files), size=format_size(scan.total_bytes))]
    cats = scan.by_category()
    lines.append(_("  Categorie: ") + ", ".join(f"{_(c)} {len(fs)}" for c, fs in list(cats.items())[:6]))
    if scan.cloud_only_count:
        lines.append(_("  ⛅ {n} file solo su iCloud (non scaricati).", n=scan.cloud_only_count))
    stats = ctx.intelligence.index.stats(scan.root)
    if stats["documents"]:
        lines.append(_("  🧠 {n} file già analizzati nel contenuto.", n=stats["documents"]))
    if scan.duplicates_checked:
        if scan.duplicates:
            lines.append(_("  ❗ {groups} gruppi di duplicati: {copies} copie, {size} recuperabili.",
                           groups=len(scan.duplicates), copies=scan.duplicate_file_count,
                           size=format_size(scan.wasted_bytes)))
        else:
            lines.append(_("  ✅ Nessun duplicato."))
    lines.append("\n" + _("Posso: 'trovami i duplicati', 'mostrami i 10 file più grandi', "
                           "'indicizza la cartella' (per cercare nel contenuto), 'organizza i file'."))
    return CommandResult("\n".join(lines), summary_data(ctx, scan))
