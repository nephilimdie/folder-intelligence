from ..intents import Intent
from .base import Command, CommandResult, Request


class IndexCommand(Command):
    name = "index"
    description = "Legge e comprende il contenuto dei file (testo, PDF, immagini, Office, audio/video)"
    examples = ("indicizza la cartella", "analizza il contenuto dei file")
    keywords = ("indicizza", "indicizzare", "analizza il contenuto", "analizza i contenuti", "leggi i file",
                "leggi il contenuto", "comprendi", "studia i file", "impara", "aggiorna l'indice")
    priority = 33
    params = ("limit",)

    def match(self, req: Request):
        if not req.has(*self.keywords):
            return None
        m = req.search(r"\b(?:primi|max|massimo)\s+(\d{1,5})\b")
        return {"limit": int(m.group(1))} if m else {}

    async def run(self, ctx, intent: Intent) -> str:
        scan = await ctx.get_scan(intent.path)
        limit = int(intent.args.get("limit") or ctx.index_limit)
        engine = ctx.intelligence
        ai = ctx.ai
        caps = []
        if ai and await ai.is_available():
            caps.append(f"comprensione {ai.model}")
        if ai and await ai.can_see() and engine.use_vision:
            caps.append(f"visione {ai.vision_model}")
        if ai and await ai.can_embed():
            caps.append(f"ricerca semantica {ai.embed_model}")
        ctx.status("Leggo i contenuti" + (f" ({', '.join(caps)})" if caps else " (senza AI)") + " …")
        files = sorted((f for f in scan.files), key=lambda f: -f.modified)  # i più recenti prima
        stats = await engine.analyze_many([f.path for f in files], limit=limit,
                                          progress=ctx.progress, cancel=ctx.cancel)
        engine.index.remove_missing(scan.root)
        scan.classified = False
        lines = [f"🧠 Analizzati {stats.analyzed} file in {stats.seconds:.0f}s."]
        if stats.skipped_fresh:
            lines.append(f"  {stats.skipped_fresh} già in indice e non modificati.")
        if stats.skipped_cloud:
            lines.append(f"  ☁️  {stats.skipped_cloud} solo su iCloud: saltati (scaricali per analizzarli).")
        remaining = len(scan.files) - stats.analyzed - stats.skipped_fresh - stats.skipped_cloud
        if remaining > 0:
            lines.append(f"  {remaining} ancora da analizzare: ripeti 'indicizza' per continuare "
                         f"(massimo {limit} per volta).")
        if stats.engines:
            lines.append("  Motori usati: " + ", ".join(f"{k} {v}" for k, v in
                                                        sorted(stats.engines.items(), key=lambda kv: -kv[1])))
        if stats.errors:
            lines.append(f"  ⚠️  {len(stats.errors)} errori, es. {stats.errors[0]}")
        mcp = await ctx.sync_mcp(scan.root)
        if mcp:
            lines.append("  " + mcp)
        lines.append("\nOra puoi cercare per contenuto: es. 'cerca la ricevuta dell'hotel' "
                     "o chiedere 'quanto ho speso di bollette?'.")
        return CommandResult("\n".join(lines), {
            "root": str(scan.root), "analyzed": stats.analyzed, "skipped_fresh": stats.skipped_fresh,
            "skipped_cloud": stats.skipped_cloud, "remaining": max(0, remaining), "engines": stats.engines,
            "errors": stats.errors[:50], "seconds": round(stats.seconds, 1), "mcp": mcp})
