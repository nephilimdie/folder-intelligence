import re

from ..i18n import N_, _
from ..intents import _FOLDER_WORD_RE, Intent, normalize
from ..models import format_size
from ..threads import run_blocking
from .base import Command, CommandResult, Request

SIMILAR_WORDS = ("simil", "somigli", "assomigl", "quasi uguali", "quasi identic", "quasi doppi",
                 "versioni", "raffica", "raffiche", "similar", "alike", "near-duplicate", "near duplicate",
                 "almost identical", "almost the same", "versions", "burst")
IMAGE_WORDS = ("foto", "immagin", "scatti", "screenshot", "fotografi", "picture", "photo", "image", "shot")
DOC_WORDS = ("document", "pdf", "testi", "contratt", "version", "word", "fogli", "excel", "presentazion",
             "text", "contract", "spreadsheet", "presentation", "sheet")
SHOW_GROUPS = 12
SHOW_FILES = 6


class SimilarCommand(Command):
    name = "similar"
    group = "analisi"
    description = N_("Trova foto e documenti simili ma non identici (foto ridimensionate o ripetute, "
                     "stesso documento in formati o versioni diverse)")
    examples = (N_("trova foto simili"), N_("trova elementi simili in Documents"), N_("ci sono documenti quasi uguali?"))
    priority = 27
    params = ("kind", "strictness")

    def match(self, req: Request):
        if not req.has(*SIMILAR_WORDS):
            return None
        # "in ~/Documents" o "nella cartella Immagini" indicano dove cercare, non cosa
        what = _FOLDER_WORD_RE.sub(" ", req.text)
        what = normalize(re.sub(r"\S*/\S*", " ", what))
        images = any(w in what for w in IMAGE_WORDS)
        docs = any(w in what for w in DOC_WORDS)
        kind = "images" if images and not docs else "documents" if docs and not images else "all"
        strict = "strict" if req.has("molto simil", "quasi identic", "praticamente uguali", "very similar",
                                     "almost identical", "nearly identical") else \
            "loose" if req.has("vagamente", "un po'", "lontanamente", "vaguely", "somewhat", "loosely") else "normal"
        return {"kind": kind, "strictness": strict}

    async def run(self, ctx, intent: Intent) -> str:
        from .. import similarity as sim

        kind = intent.args.get("kind", "all")
        if kind not in ("all", "images", "documents"):
            kind = "all"
        threshold = {"strict": sim.STRICT, "loose": sim.LOOSE}.get(intent.args.get("strictness"), sim.NORMAL)
        scan = await ctx.get_scan(intent.path)
        index = ctx.intelligence.index

        # i documenti si confrontano sul testo: se la cartella non è mai stata letta, chiediamolo
        if kind == "documents":
            question = await ctx.require_index(scan, lambda: self.run(ctx, intent),
                                               _("confrontare il contenuto dei documenti"))
            if question:
                return question

        what = {"images": N_("le foto"), "documents": N_("i documenti")}.get(kind, N_("foto e documenti"))
        ctx.status(_("Cerco {what} simili in {root} …", what=_(what), root=scan.root))
        res = await run_blocking(sim.find_similar, scan.files, index, kind, threshold,
                                 ctx.progress, ctx.cancel)

        data = {"root": str(scan.root), "kind": kind, "threshold": threshold,
                "groups": [g.to_dict() for g in res.groups],
                "images_checked": res.images_checked, "images_skipped": res.images_skipped,
                "documents_checked": res.documents_checked,
                "documents_not_indexed": res.documents_not_indexed, "errors": res.errors[:50]}

        lines: list[str] = []
        photo_groups = [g for g in res.groups if g.kind == "foto"]
        doc_groups = [g for g in res.groups if g.kind == "documenti"]
        if not res.groups:
            lines.append(_("Nessun elemento simile trovato in {root}. ✅", root=scan.root))
        else:
            parts = []
            if photo_groups:
                parts.append(_("{n} gruppi di foto", n=len(photo_groups)))
            if doc_groups:
                parts.append(_("{n} gruppi di documenti", n=len(doc_groups)))
            lines.append(_("Ho trovato {what} simili in {root}:", what=_(" e ").join(parts), root=scan.root))
            for i, g in enumerate(res.groups[:SHOW_GROUPS], 1):
                icon = "📷 " if g.kind == "foto" else "📄"
                total = sum(f.size_bytes for f in g.files)
                lines.append(f"\n[{i}] {icon} {g.reason} — " + _("{n} file", n=len(g.files)) + f", {format_size(total)}")
                for f in g.files[:SHOW_FILES]:
                    note = g.details.get(str(f.path), "")
                    lines.append(f"    {self.rel(f.path, scan.root)}" + (f"  · {note}" if note else ""))
                if len(g.files) > SHOW_FILES:
                    lines.append(_("    … e altri {n}", n=len(g.files) - SHOW_FILES))
            if len(res.groups) > SHOW_GROUPS:
                lines.append("\n" + _("… e altri {n} gruppi (l'elenco completo è nei dati / nell'API).",
                                       n=len(res.groups) - SHOW_GROUPS))
            lines.append("\n" + _("Sono suggerimenti: non sposto né cancello nulla. ⭐ = quello che terrei io. "
                                   "Controlla e decidi tu."))

        notes = []
        if kind in ("all", "images"):
            notes.append(_("foto confrontate: {n}", n=res.images_checked))
            if res.images_skipped:
                notes.append(_("{n} foto più vecchie non controllate (limite {limit})",
                               n=res.images_skipped, limit=sim.MAX_IMAGES))
        if kind in ("all", "documents") and res.documents_checked:
            notes.append(_("documenti: {n}", n=res.documents_checked))
            if res.documents_not_indexed:
                notes.append(_("{n} non ancora letti (confrontati solo per nome: scrivi 'indicizza la cartella' "
                               "per confrontarne anche il testo)", n=res.documents_not_indexed))
        if scan.cloud_only_count:
            notes.append(_("{n} file solo su iCloud saltati", n=scan.cloud_only_count))
        if res.errors:
            notes.append(_("{n} file non leggibili", n=len(res.errors)))
        if notes:
            lines.append("\n💡 " + " · ".join(notes))
        return CommandResult("\n".join(lines), data)
