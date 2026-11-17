import re
from pathlib import Path

from ..intelligence import CloudOnly
from ..intents import Intent
from ..models import format_size
from .base import Command, CommandResult, Request

FILE_RE = r"([\w\-. ()]+\.[A-Za-z0-9]{2,5})\b"


class DescribeCommand(Command):
    name = "describe"
    description = "Spiega cosa contiene un file (legge testo, immagini, PDF…)"
    examples = ("cosa contiene fattura.pdf?", "descrivi IMG_2041.HEIC")
    keywords = ("cosa contiene", "descrivi", "di cosa parla", "cos'e", "cosa c'e in", "cosa c'e nel",
                "riassumi", "riassunto di", "leggimi")
    priority = 28
    params = ("file",)

    def match(self, req: Request):
        if not req.has(*self.keywords):
            return None
        file_paths = [p for p in req.paths if p.suffix]
        if file_paths:
            return {"path": None, "file": str(file_paths[0])}
        m = re.search(FILE_RE, req.text)
        if m:
            name = re.sub(r"^.*?(contiene|descrivi|parla|cos'è|cos'e|c'è in|c'è nel|riassumi|riassunto di|leggimi)\s+",
                          "", m.group(1), flags=re.I).strip()
            return {"path": None, "file": name}
        return None

    async def run(self, ctx, intent: Intent) -> str:
        target = await self._locate(ctx, str(intent.args.get("file", "")))
        if isinstance(target, str):
            return target
        ctx.status(f"Leggo {target.name} …")
        try:
            doc = await ctx.intelligence.analyze(target)
        except CloudOnly as e:
            return f"☁️  {e}."
        meta = doc.get("metadata") or {}
        lines = [f"📄 {doc.get('title') or target.name}  ({target.name}, {format_size(doc.get('size') or 0)})",
                 f"   Categoria: {doc.get('category')}   ·   letto con: {doc.get('engine')}"]
        if doc.get("description"):
            lines.append(f"\n{doc['description']}")
        if doc.get("tags"):
            lines.append("\n🏷️  " + ", ".join(doc["tags"]))
        facts = []
        for key, label in (("date", "data"), ("amount", "importo"), ("NumberOfPages", "pagine"),
                           ("pages", "pagine"), ("duration_s", "durata (s)"), ("resolution", "risoluzione"),
                           ("Authors", "autori"), ("AcquisitionModel", "fotocamera")):
            if meta.get(key):
                facts.append(f"{label}: {meta[key]}")
        if meta.get("WhereFroms"):
            src = meta["WhereFroms"]
            facts.append(f"scaricato da: {src[0] if isinstance(src, list) else src}")
        if facts:
            lines.append("ℹ️  " + " · ".join(facts))
        text = (doc.get("text") or "").strip()
        if text:
            excerpt = re.sub(r"\s+", " ", text)[:400]
            lines.append(f"\n«{excerpt}{'…' if len(text) > 400 else ''}»")
        return CommandResult("\n".join(lines), {"document": doc})

    async def _locate(self, ctx, ref: str) -> Path | str:
        if not ref:
            return "Quale file? Es. 'cosa contiene fattura.pdf?'"
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
            return f"Non trovo '{ref}' in {scan.root}. Prova con il percorso completo o 'cerca {Path(ref).stem}'."
        if len(found) > 1 and not exact:
            opts = "\n".join(f"  • {self.rel(f, scan.root)}" for f in found[:8])
            return f"Ho trovato più file simili, quale intendi?\n{opts}"
        return found[0]
