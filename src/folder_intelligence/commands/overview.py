import re
from collections import Counter

from ..intents import Intent
from .base import Command, CommandResult, Request

FILE_REF = re.compile(r"[\w\-()]+\.[A-Za-z0-9]{2,5}\b")
MAX_DOCS = 80


class OverviewCommand(Command):
    name = "overview"
    description = "Riassunto dei contenuti di una cartella: temi, tipi di documenti, periodi, cose importanti"
    examples = ("cosa contiene la cartella Documents, fammi un riassunto", "riassumi il contenuto della cartella")
    priority = 29

    def match(self, req: Request):
        asks_summary = req.has("riassunt", "riassumi", "panoramica dei contenuti", "di cosa parla la cartella",
                               "cosa contiene la cartella", "cosa c'e nella cartella", "di cosa parlano")
        if asks_summary and not FILE_REF.search(req.text):
            return {}
        return None

    async def run(self, ctx, intent: Intent) -> str:
        scan = await ctx.get_scan(intent.path)
        if intent.path is not None:
            ctx.directory = scan.root
        question = await ctx.require_index(scan, lambda: self.run(ctx, intent), "riassumerne il contenuto")
        if question:
            return question
        index = ctx.intelligence.index
        rows = sorted(index.under(scan.root).items(), key=lambda kv: kv[0])
        docs = [index.get(p) for p, _ in rows]
        docs = sorted((d for d in docs if d), key=lambda d: -(d.get("mtime") or 0))
        cats = Counter(d.get("category") or "altro" for d in docs)
        types = Counter((d.get("metadata") or {}).get("doc_type") for d in docs if (d.get("metadata") or {}).get("doc_type"))
        lines = [f"📚 {scan.root}: {len(docs)} file letti su {len(scan.files)}."]
        lines.append("   Categorie: " + ", ".join(f"{c} {n}" for c, n in cats.most_common(6)))
        if types:
            lines.append("   Tipi: " + ", ".join(f"{t} {n}" for t, n in types.most_common(8)))

        ai = ctx.ai
        if ai and await ai.is_available():
            ctx.status("Preparo il riassunto …")
            listing = "\n".join(f"- {d.get('title') or d.get('name')}: {(d.get('description') or '')[:160]}"
                                + (f" [{(d.get('metadata') or {}).get('date')}]" if (d.get('metadata') or {}).get('date') else "")
                                for d in docs[:MAX_DOCS])
            try:
                summary = await ai.chat([
                    {"role": "system", "content": "Sei un archivista. Rispondi in italiano, conciso (max 12 righe), "
                                                  "con elenco puntato. Basati solo sull'elenco fornito."},
                    {"role": "user", "content": f"Ecco i documenti di una cartella:\n{listing}\n\n"
                                                "Riassumi cosa contiene: temi principali, tipi di documenti, periodo "
                                                "coperto, eventuali scadenze o importi rilevanti."},
                ])
                if summary:
                    lines.append("\n" + summary)
            except Exception:
                pass
        else:
            lines.append("\nDocumenti più recenti:")
            lines += [f"  • {d.get('title') or d.get('name')}" for d in docs[:10]]
        not_read = len(scan.files) - len(docs)
        if not_read > 0:
            lines.append(f"\n({not_read} file non ancora letti: 'indicizza la cartella' per continuare.)")
        lines.append("Puoi chiedermi dettagli, es. 'cerca …' o una domanda diretta.")
        return CommandResult("\n".join(lines), {"root": str(scan.root), "documents": len(docs),
                             "files": len(scan.files), "categories": dict(cats), "doc_types": dict(types)})
