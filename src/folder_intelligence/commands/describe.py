import re
from pathlib import Path

from ..intelligence import CloudOnly
from ..i18n import N_, _
from ..intents import Intent
from ..models import format_size
from .base import Command, CommandResult, Request

FILE_RE = r"([\w\-. ()]+\.[A-Za-z0-9]{2,5})\b"


class DescribeCommand(Command):
    name = "describe"
    group = "analisi"
    description = N_("Spiega cosa contiene un file (legge testo, immagini, PDF…)")
    examples = (N_("cosa contiene fattura.pdf?"), N_("descrivi IMG_2041.HEIC"))
    keywords = ("cosa contiene", "descrivi", "di cosa parla", "cos'e", "cosa c'e in", "cosa c'e nel",
                "riassumi", "riassunto di", "leggimi", "what's in", "what is in", "whats in", "describe",
                "what does", "summarize", "summary of", "read me")
    priority = 28
    params = ("file",)

    def match(self, req: Request):
        if not req.has(*self.keywords):
            return None
        file_paths = [p for p in req.paths if p.suffix]
        if file_paths:
            return {"path": None, "file": str(file_paths[0])}
        # prima si toglie la parte iniziale ("cosa contiene", "what's in"…), poi si cerca il nome del file:
        # altrimenti "what's in note.txt" darebbe "s in note.txt"
        rest = re.sub(r"^.*?\b(contiene|descrivi|parla di|parla|cos'è|cos'e|c'è in|c'è nel|riassumi|riassunto di|"
                      r"leggimi|what'?s in|what is in|describe|summarize|summary of|read me|about)\s+",
                      "", req.text, count=1, flags=re.I)
        m = re.search(FILE_RE, rest)
        if m:
            return {"path": None, "file": m.group(1).strip()}
        return None

    async def run(self, ctx, intent: Intent) -> str:
        target = await self._locate(ctx, str(intent.args.get("file", "")))
        if isinstance(target, str):
            return target
        ctx.status(_("Leggo {name} …", name=target.name))
        try:
            doc = await ctx.intelligence.analyze(target)
        except CloudOnly as e:
            return f"☁️  {e}."
        meta = doc.get("metadata") or {}
        lines = [f"📄 {doc.get('title') or target.name}  ({target.name}, {format_size(doc.get('size') or 0)})",
                 _("   Categoria: {cat}   ·   letto con: {engine}", cat=_(doc.get("category") or "altro"),
                   engine=doc.get("engine"))]
        if doc.get("description"):
            lines.append(f"\n{doc['description']}")
        if doc.get("tags"):
            lines.append("\n🏷️  " + ", ".join(doc["tags"]))
        facts = []
        for key, label in (("date", N_("data")), ("amount", N_("importo")), ("NumberOfPages", N_("pagine")),
                           ("pages", N_("pagine")), ("duration_s", N_("durata (s)")), ("resolution", N_("risoluzione")),
                           ("Authors", N_("autori")), ("AcquisitionModel", N_("fotocamera"))):
            if meta.get(key):
                facts.append(f"{_(label)}: {meta[key]}")
        if meta.get("WhereFroms"):
            src = meta["WhereFroms"]
            facts.append(_("scaricato da: {src}", src=src[0] if isinstance(src, list) else src))
        if facts:
            lines.append("ℹ️  " + " · ".join(facts))
        text = (doc.get("text") or "").strip()
        if text:
            excerpt = re.sub(r"\s+", " ", text)[:400]
            lines.append(f"\n«{excerpt}{'…' if len(text) > 400 else ''}»")
        return CommandResult("\n".join(lines), {"document": doc})

    async def _locate(self, ctx, ref: str) -> Path | str:
        if not ref:
            return _("Quale file? Es. 'cosa contiene fattura.pdf?'")
        p = Path(ref).expanduser()
        if not p.is_absolute():
            p = ctx.directory / p
        if p.is_file():
            return p
        scan = await ctx.get_scan(None)
        name = Path(ref).name.lower()
        exact = [f.path for f in scan.files if f.name.lower() == name]
        partial = [f.path for f in scan.files if name in f.name.lower()]
        found = exact or partial
        if not found:
            return _("Non trovo '{ref}' in {root}. Prova con il percorso completo o 'cerca {stem}'.",
                     ref=ref, root=scan.root, stem=Path(ref).stem)
        if len(found) > 1 and not exact:
            opts = "\n".join(f"  • {self.rel(f, scan.root)}" for f in found[:8])
            return _("Ho trovato più file simili, quale intendi?") + f"\n{opts}"
        return found[0]
