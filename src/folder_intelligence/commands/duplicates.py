import re
import time

from ..i18n import N_, _
from ..intents import Intent
from ..models import format_size
from .base import Command, CommandResult, Request

DUP_WORDS = ("duplicat", "doppion", "copie", "uguali", "ripetut", "duplicate", "dupes", "copies", "identical")
# "cancella tutto" / "eliminali" valgono come dedupe solo se la frase non dice altro
# (es. "cancella tutti i file pdf" NON deve diventare "cancella i duplicati")
_BARE_DELETE = re.compile(r"(cancella|elimina|rimuovi|butta|cestina)(li|le)?"
                          r"( (pure|tutto|tutti|tutte|quelli|quelle|via))*"
                          r"|(delete|remove|trash|bin) (them|them all|all|all of them|everything)")
MAX_SCAN_AGE = 120  # s: oltre, i file potrebbero essere cambiati dal Finder → riscansione


async def fresh_scan(ctx, path):
    scan = await ctx.get_scan(path)
    if time.time() - getattr(scan, "created", time.time()) > MAX_SCAN_AGE:
        scan = await ctx.get_scan(path, refresh=True)
    return scan


def _note(g, f) -> str:
    return "  " + _("(hardlink/clone APFS: non occupa spazio in più)") if g.shares_storage(f) else ""


class DuplicatesCommand(Command):
    name = "duplicates"
    group = "analisi"
    description = N_("Trova i file duplicati (stesso contenuto)")
    examples = (N_("trovami i duplicati"), N_("ci sono doppioni nella cartella Downloads?"))
    keywords = DUP_WORDS
    priority = 31

    async def run(self, ctx, intent: Intent) -> str:
        scan = await fresh_scan(ctx, intent.path)
        await ctx.get_duplicates(scan)
        data = {"root": str(scan.root), "groups": [g.to_dict() for g in scan.duplicates],
                "wasted_bytes": scan.wasted_bytes, "skipped": [e for e in scan.errors if "timeout" in e][:50],
                "cloud_only_files": scan.cloud_only_count}
        if not scan.duplicates:
            return CommandResult(_("Nessun file duplicato in {root}. ✅", root=scan.root) + ctx.skipped_note(scan), data)
        lines = [_("Trovati {groups} gruppi di duplicati in {root} ({copies} copie, {size} recuperabili):",
                   groups=len(scan.duplicates), root=scan.root, copies=scan.duplicate_file_count,
                   size=format_size(scan.wasted_bytes))]
        for i, g in enumerate(scan.duplicates[:10], 1):
            lines.append(f"\n[{i}] {g.keep.name} — {format_size(g.size_bytes)} × {len(g.files)} ({_(g.keep.category)})")
            lines.append(_("    ✅ tengo:  {path}", path=self.rel(g.keep.path, scan.root)))
            for f in g.extras[:5]:
                lines.append(_("    🗑️  copia: {path}", path=self.rel(f.path, scan.root)) + _note(g, f))
            if len(g.extras) > 5:
                lines.append(_("    … e altre {n} copie", n=len(g.extras) - 5))
        if len(scan.duplicates) > 10:
            lines.append("\n" + _("… e altri {n} gruppi (usa 'esporta report' per l'elenco completo).",
                                  n=len(scan.duplicates) - 10))
        lines.append(ctx.skipped_note(scan).strip() or "")
        lines.append("\n" + _("Come vuoi procedere? 'cancella i duplicati' (vanno nel Cestino), "
                               "'organizza i file' o 'esporta il report'."))
        return CommandResult("\n".join(line for line in lines if line is not None), data)


class DedupeCommand(Command):
    name = "dedupe"
    group = "organizzazione"
    description = N_("Sposta nel Cestino le copie duplicate (con conferma e audit)")
    examples = (N_("cancella i duplicati"),)
    priority = 30
    destructive = True

    def match(self, req: Request):
        is_delete = req.has("cancella", "elimina", "rimuovi", "butta", "cestina", "pulisci", "delete", "remove",
                            "trash", "clean up", "get rid of")
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
            return _("Non ci sono duplicati da rimuovere. ✅")
        plan = organizer.plan_dedupe(scan.duplicates, scan.root)
        ctx.pending = plan
        lines = [_("Sposterò nel Cestino {n} copie ({size}), tenendo un originale per ogni gruppo:",
                   n=len(plan), size=format_size(scan.wasted_bytes))]
        lines += [f"  🗑️  {self.rel(m.src, scan.root)}" for m in plan.moves[:15]]
        if len(plan) > 15:
            lines.append(_("  … e altri {n} file", n=len(plan) - 15))
        lines.append(_("Prima di spostare ogni copia ricontrollo che sia ancora identica all'originale."))
        lines.append("\n" + _("Confermi? (sì/no) — potrai sempre annullare con 'annulla l'ultima operazione'."))
        return CommandResult("\n".join(lines), {"plan": {"kind": "dedupe", "count": len(plan),
                             "moves": [{"src": m.src, "dst": m.dst} for m in plan.moves]}})
