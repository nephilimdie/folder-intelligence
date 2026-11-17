import re
import time

from ..intents import Intent
from ..models import format_size
from .base import Command, CommandResult, Request

DUP_WORDS = ("duplicat", "doppion", "copie", "uguali", "ripetut")
# "cancella tutto" / "eliminali" valgono come dedupe solo se la frase non dice altro
# (es. "cancella tutti i file pdf" NON deve diventare "cancella i duplicati")
_BARE_DELETE = re.compile(r"(cancella|elimina|rimuovi|butta|cestina)(li|le)?"
                          r"( (pure|tutto|tutti|tutte|quelli|quelle|via))*")
MAX_SCAN_AGE = 120  # s: oltre, i file potrebbero essere cambiati dal Finder → riscansione


async def fresh_scan(ctx, path):
    scan = await ctx.get_scan(path)
    if time.time() - getattr(scan, "created", time.time()) > MAX_SCAN_AGE:
        scan = await ctx.get_scan(path, refresh=True)
    return scan


def _note(g, f) -> str:
    return "  (hardlink/clone APFS: non occupa spazio in più)" if g.shares_storage(f) else ""


class DuplicatesCommand(Command):
    name = "duplicates"
    description = "Trova i file duplicati (stesso contenuto)"
    examples = ("trovami i duplicati", "ci sono doppioni nella cartella Downloads?")
    keywords = DUP_WORDS
    priority = 31

    async def run(self, ctx, intent: Intent) -> str:
        scan = await fresh_scan(ctx, intent.path)
        await ctx.get_duplicates(scan)
        data = {"root": str(scan.root), "groups": [g.to_dict() for g in scan.duplicates],
                "wasted_bytes": scan.wasted_bytes, "skipped": [e for e in scan.errors if "timeout" in e][:50],
                "cloud_only_files": scan.cloud_only_count}
        if not scan.duplicates:
            return CommandResult(f"Nessun file duplicato in {scan.root}. ✅" + ctx.skipped_note(scan), data)
        lines = [f"Trovati {len(scan.duplicates)} gruppi di duplicati in {scan.root} "
                 f"({scan.duplicate_file_count} copie, {format_size(scan.wasted_bytes)} recuperabili):"]
        for i, g in enumerate(scan.duplicates[:10], 1):
            lines.append(f"\n[{i}] {g.keep.name} — {format_size(g.size_bytes)} × {len(g.files)} ({g.keep.category})")
            lines.append(f"    ✅ tengo:  {self.rel(g.keep.path, scan.root)}")
            for f in g.extras[:5]:
                lines.append(f"    🗑️  copia: {self.rel(f.path, scan.root)}{_note(g, f)}")
            if len(g.extras) > 5:
                lines.append(f"    … e altre {len(g.extras) - 5} copie")
        if len(scan.duplicates) > 10:
            lines.append(f"\n… e altri {len(scan.duplicates) - 10} gruppi (usa 'esporta report' per l'elenco completo).")
        lines.append(ctx.skipped_note(scan).strip() or "")
        lines.append("\nCome vuoi procedere? 'cancella i duplicati' (vanno nel Cestino), "
                     "'organizza i file' o 'esporta il report'.")
        return CommandResult("\n".join(line for line in lines if line is not None), data)


class DedupeCommand(Command):
    name = "dedupe"
    description = "Sposta nel Cestino le copie duplicate (con conferma e audit)"
    examples = ("cancella i duplicati",)
    priority = 30
    destructive = True

    def match(self, req: Request):
        is_delete = req.has("cancella", "elimina", "rimuovi", "butta", "cestina", "pulisci", "delete")
        if is_delete and req.has(*DUP_WORDS):
            return {}
        if _BARE_DELETE.fullmatch(req.norm.strip(" ?!.")):
            return {}
        return None

    async def run(self, ctx, intent: Intent) -> str:
        from .. import organizer

        scan = await fresh_scan(ctx, intent.path)
        await ctx.get_duplicates(scan)
        if not scan.duplicates:
            return "Non ci sono duplicati da rimuovere. ✅"
        plan = organizer.plan_dedupe(scan.duplicates, scan.root)
        ctx.pending = plan
        lines = [f"Sposterò nel Cestino {len(plan)} copie ({format_size(scan.wasted_bytes)}), "
                 "tenendo un originale per ogni gruppo:"]
        lines += [f"  🗑️  {self.rel(m.src, scan.root)}" for m in plan.moves[:15]]
        if len(plan) > 15:
            lines.append(f"  … e altri {len(plan) - 15} file")
        lines.append("Prima di spostare ogni copia ricontrollo che sia ancora identica all'originale.")
        lines.append("\nConfermi? (sì/no) — potrai sempre annullare con 'annulla l'ultima operazione'.")
        return CommandResult("\n".join(lines), {"plan": {"kind": "dedupe", "count": len(plan),
                             "moves": [{"src": m.src, "dst": m.dst} for m in plan.moves]}})
