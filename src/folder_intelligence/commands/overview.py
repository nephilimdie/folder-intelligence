import re
from collections import Counter

from ..i18n import N_, _, language_name
from ..intents import Intent
from .base import Command, CommandResult, Request

FILE_REF = re.compile(r"[\w\-()]+\.[A-Za-z0-9]{2,5}\b")
MAX_DOCS = 80


class OverviewCommand(Command):
    name = "overview"
    group = "analisi"
    description = N_("Riassunto dei contenuti di una cartella: temi, tipi di documenti, periodi, cose importanti")
    examples = (N_("cosa contiene la cartella Documents, fammi un riassunto"),
                N_("riassumi il contenuto della cartella"))
    priority = 29

    def match(self, req: Request):
        asks_summary = req.has("riassunt", "riassumi", "panoramica dei contenuti", "di cosa parla la cartella",
                               "cosa contiene la cartella", "cosa c'e nella cartella", "di cosa parlano",
                               "summarize", "summarise", "give me a summary", "overview of")
        asks_summary = asks_summary or bool(req.search(r"\bwhat(?:'s| is) in (?:the |my )?[\w~/.-]* ?folder\b"))
        if asks_summary and not FILE_REF.search(req.text):
            return {}
        return None

    async def run(self, ctx, intent: Intent) -> str:
        scan = await ctx.get_scan(intent.path)
        if intent.path is not None:
            ctx.directory = scan.root
        question = await ctx.require_index(scan, lambda: self.run(ctx, intent), _("riassumerne il contenuto"))
        if question:
            return question
        index = ctx.intelligence.index
        rows = sorted(index.under(scan.root).items(), key=lambda kv: kv[0])
        docs = [index.get(p) for p, _row in rows]
        docs = sorted((d for d in docs if d), key=lambda d: -(d.get("mtime") or 0))
        cats = Counter(d.get("category") or "altro" for d in docs)
        types = Counter((d.get("metadata") or {}).get("doc_type") for d in docs if (d.get("metadata") or {}).get("doc_type"))
        lines = [_("📚 {root}: {n} file letti su {total}.", root=scan.root, n=len(docs), total=len(scan.files))]
        lines.append(_("   Categorie: ") + ", ".join(f"{_(c)} {n}" for c, n in cats.most_common(6)))
        if types:
            lines.append(_("   Tipi: ") + ", ".join(f"{t} {n}" for t, n in types.most_common(8)))

        ai = ctx.ai
        if ai and await ai.is_available():
            ctx.status(_("Preparo il riassunto …"))
            listing = "\n".join(f"- {d.get('title') or d.get('name')}: {(d.get('description') or '')[:160]}"
                                + (f" [{(d.get('metadata') or {}).get('date')}]" if (d.get('metadata') or {}).get('date') else "")
                                for d in docs[:MAX_DOCS])
            try:
                summary = await ai.chat([
                    {"role": "system", "content": "You are an archivist. Answer in " + language_name() +
                                                  ", concisely (max 12 lines), as a bulleted list. Use only "
                                                  "the list provided."},
                    {"role": "user", "content": f"These are the documents in a folder:\n{listing}\n\n"
                                                "Summarize what it contains: main topics, document types, period "
                                                "covered, any relevant deadlines or amounts."},
                ])
                if summary:
                    lines.append("\n" + summary)
            except Exception:
                pass
        else:
            lines.append("\n" + _("Documenti più recenti:"))
            lines += [f"  • {d.get('title') or d.get('name')}" for d in docs[:10]]
        not_read = len(scan.files) - len(docs)
        if not_read > 0:
            lines.append("\n" + _("({n} file non ancora letti: 'indicizza la cartella' per continuare.)", n=not_read))
        lines.append(_("Puoi chiedermi dettagli, es. 'cerca …' o una domanda diretta."))
        return CommandResult("\n".join(lines), {"root": str(scan.root), "documents": len(docs),
                             "files": len(scan.files), "categories": dict(cats), "doc_types": dict(types)})
