import re

from ..intents import _FOLDER_WORD_RE, Intent, normalize
from ..models import format_size
from ..threads import run_blocking
from .base import Command, CommandResult, Request

SIMILAR_WORDS = ("simil", "somigli", "assomigl", "quasi uguali", "quasi identic", "quasi doppi",
                 "versioni", "raffica", "raffiche")
IMAGE_WORDS = ("foto", "immagin", "scatti", "screenshot", "fotografi", "picture", "photo")
DOC_WORDS = ("document", "pdf", "testi", "contratt", "version", "word", "fogli", "excel", "presentazion")
SHOW_GROUPS = 12
SHOW_FILES = 6


class SimilarCommand(Command):
    name = "similar"
    group = "analisi"
    description = ("Trova foto e documenti simili ma non identici (foto ridimensionate o ripetute, "
                   "stesso documento in formati o versioni diverse)")
    examples = ("trova foto simili", "trova elementi simili in Documents", "ci sono documenti quasi uguali?")
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
        strict = "strict" if req.has("molto simil", "quasi identic", "praticamente uguali") else \
            "loose" if req.has("vagamente", "un po'", "lontanamente") else "normal"
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
                                               "confrontare il contenuto dei documenti")
            if question:
                return question

        what = {"images": "le foto", "documents": "i documenti"}.get(kind, "foto e documenti")
        ctx.status(f"Cerco {what} simili in {scan.root} …")
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
            lines.append(f"Nessun elemento simile trovato in {scan.root}. ✅")
        else:
            parts = []
            if photo_groups:
                parts.append(f"{len(photo_groups)} gruppi di foto")
            if doc_groups:
                parts.append(f"{len(doc_groups)} gruppi di documenti")
            lines.append(f"Ho trovato {' e '.join(parts)} simili in {scan.root}:")
            for i, g in enumerate(res.groups[:SHOW_GROUPS], 1):
                icon = "🖼️ " if g.kind == "foto" else "📄"
                total = sum(f.size_bytes for f in g.files)
                lines.append(f"\n[{i}] {icon} {g.reason} — {len(g.files)} file, {format_size(total)}")
                for f in g.files[:SHOW_FILES]:
                    note = g.details.get(str(f.path), "")
                    lines.append(f"    {self.rel(f.path, scan.root)}" + (f"  · {note}" if note else ""))
                if len(g.files) > SHOW_FILES:
                    lines.append(f"    … e altri {len(g.files) - SHOW_FILES}")
            if len(res.groups) > SHOW_GROUPS:
                lines.append(f"\n… e altri {len(res.groups) - SHOW_GROUPS} gruppi "
                             "(l'elenco completo è nei dati / nell'API).")
            lines.append("\nSono suggerimenti: non sposto né cancello nulla. ⭐ = quello che terrei io. "
                         "Controlla e decidi tu.")

        notes = []
        if kind in ("all", "images"):
            notes.append(f"foto confrontate: {res.images_checked}")
            if res.images_skipped:
                notes.append(f"{res.images_skipped} foto più vecchie non controllate (limite {sim.MAX_IMAGES})")
        if kind in ("all", "documents") and res.documents_checked:
            notes.append(f"documenti: {res.documents_checked}")
            if res.documents_not_indexed:
                notes.append(f"{res.documents_not_indexed} non ancora letti (confrontati solo per nome: "
                             f"scrivi 'indicizza la cartella' per confrontarne anche il testo)")
        if scan.cloud_only_count:
            notes.append(f"{scan.cloud_only_count} file solo su iCloud saltati")
        if res.errors:
            notes.append(f"{len(res.errors)} file non leggibili")
        if notes:
            lines.append("\nℹ️  " + " · ".join(notes))
        return CommandResult("\n".join(lines), data)
