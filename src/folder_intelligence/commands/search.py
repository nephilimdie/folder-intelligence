import re

from ..intents import Intent, normalize
from ..models import format_size
from .base import Command, CommandResult, Request


class SearchCommand(Command):
    name = "search"
    group = "ricerca"
    description = "Cerca file per nome, contenuto e significato (anche immagini e PDF analizzati)"
    examples = ("cerca la ricevuta dell'hotel di Roma", "trova le foto della spiaggia")
    priority = 40
    params = ("query",)

    def match(self, req: Request):
        m = req.search(r"\b(?:cerca|trova|trovami|dove (?:e|sono|si trova|ho messo)|mostrami|file che parlano di)"
                       r"\s+(?:i |il |lo |la |le |gli |l')?(?:file\s+)?(.+)")
        if not m or req.has("duplicat"):
            return None
        query = re.sub(r"\b(nella|in|sulla|dentro)\s+(la\s+)?cartella.*$", "", m.group(1)).strip(" ?.!")
        return {"query": query}

    async def run(self, ctx, intent: Intent) -> str:
        query = str(intent.args.get("query", "")).strip()
        if not query:
            return "Cosa devo cercare? Es. 'cerca fattura' o 'cerca le foto del mare'."
        scan = await ctx.get_scan(intent.path)
        hits = await ctx.intelligence.search(query, scan.root, 20, names=[f.path for f in scan.files])

        # filtro rapido anche per estensione/categoria ("cerca pdf", "cerca immagini")
        terms = normalize(query).split()
        ext_terms = {f".{t.lstrip('.')}" for t in terms}
        quick = [f for f in scan.files if f.extension in ext_terms or f.category in terms]
        seen = {h.path for h in hits}
        quick = [f for f in quick if str(f.path) not in seen]

        if not hits and not quick:
            q = await ctx.require_index(scan, lambda: self.run(ctx, intent), "cercare dentro documenti, PDF e immagini")
            if q:
                return f"Nessun file ha '{query}' nel nome.\n" + q
            indexed = ctx.intelligence.index.stats(scan.root)["documents"]
            readable = sum(1 for f in scan.files if not f.cloud_only)
            msg = f"Nessun file pertinente per '{query}' in {scan.root}."
            if readable > indexed:
                msg += (f"\nHo letto il contenuto di {indexed} file su {readable}: "
                        "scrivi 'indicizza la cartella' per cercare anche negli altri.")
            return CommandResult(msg, {"query": query, "root": str(scan.root), "items": [],
                                       "indexed": indexed, "readable": readable})

        data = {"query": query, "root": str(scan.root), "items": [
            {"path": h.path, "score": round(h.score, 4), "matched_by": list(dict.fromkeys(h.sources)),
             "snippet": h.snippet, **{k: (h.doc or {}).get(k) for k in ("title", "description", "category", "tags")}}
            for h in hits], "by_type": [str(f.path) for f in quick]}
        lines = [f"Risultati per '{query}':"]
        for i, h in enumerate(hits[:15], 1):
            d = h.doc or {}
            label = d.get("title") or d.get("name") or h.path.rsplit("/", 1)[-1]
            lines.append(f"\n[{i}] {label}")
            lines.append(f"    {self.rel(h.path, scan.root)}" + (f"  · {format_size(d['size'])}" if d.get("size") else ""))
            if d.get("description"):
                lines.append(f"    {d['description'][:160]}")
            elif h.snippet:
                lines.append(f"    …{h.snippet}…")
            lines.append(f"    trovato per: {', '.join(dict.fromkeys(h.sources))}")
        for f in quick[: max(0, 25 - len(hits))]:
            lines.append(f"  {format_size(f.size_bytes):>9}  {self.rel(f.path, scan.root)}")
        if not ctx.intelligence.index.stats(scan.root)["documents"]:
            q = await ctx.require_index(scan, lambda: self.run(ctx, intent), "cercare anche dentro documenti e immagini")
            if q:
                lines.append("\n" + q)
        return CommandResult("\n".join(lines), data)
