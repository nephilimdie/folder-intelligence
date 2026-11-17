from ..i18n import N_, _
from ..intents import Intent
from .base import Command, CommandResult, Request


class IndexCommand(Command):
    name = "index"
    group = "ricerca"
    description = N_("Legge e comprende il contenuto dei file (testo, PDF, immagini, Office, audio/video)")
    examples = (N_("indicizza la cartella"), N_("analizza il contenuto dei file"))
    keywords = ("indicizza", "indicizzare", "analizza il contenuto", "analizza i contenuti", "leggi i file",
                "leggi il contenuto", "comprendi", "studia i file", "impara", "aggiorna l'indice", "index",
                "analyze the content", "analyse the content", "read the files", "understand the files",
                "update the index", "learn the files")
    priority = 33
    params = ("limit",)

    def match(self, req: Request):
        if not req.has(*self.keywords):
            return None
        m = req.search(r"\b(?:primi|max|massimo|first|maximum|up to)\s+(\d{1,5})\b")
        return {"limit": int(m.group(1))} if m else {}

    async def run(self, ctx, intent: Intent) -> str:
        scan = await ctx.get_scan(intent.path)
        limit = int(intent.args.get("limit") or ctx.index_limit or 0) or None  # None = tutti
        engine = ctx.intelligence
        ai = ctx.ai
        caps = []
        if ai and await ai.is_available():
            caps.append(_("comprensione {model}", model=ai.model))
        if ai and await ai.can_see() and engine.use_vision:
            caps.append(_("visione {model}", model=ai.vision_model))
        if ai and await ai.can_embed():
            caps.append(_("ricerca semantica {model}", model=ai.embed_model))
        ctx.status(_("Leggo i contenuti") + (f" ({', '.join(caps)})" if caps else " " + _("(senza AI)")) + " …")
        files = sorted((f for f in scan.files), key=lambda f: -f.modified)  # i più recenti prima
        stats = await engine.analyze_many([f.path for f in files], limit=limit,
                                          progress=ctx.progress, cancel=ctx.cancel)
        engine.index.remove_missing(scan.root)
        scan.classified = False
        lines = [_("🧠 Analizzati {n} file in {s}s.", n=stats.analyzed, s=f"{stats.seconds:.0f}")]
        if stats.skipped_fresh:
            lines.append(_("  {n} già in indice e non modificati.", n=stats.skipped_fresh))
        if stats.skipped_cloud:
            lines.append(_("  ☁️  {n} solo su iCloud: saltati (scaricali per analizzarli).", n=stats.skipped_cloud))
        remaining = len(scan.files) - stats.analyzed - stats.skipped_fresh - stats.skipped_cloud
        if remaining > 0 and limit:
            lines.append(_("  {n} ancora da analizzare: ripeti 'indicizza' per continuare (massimo {limit} per volta).",
                           n=remaining, limit=limit))
        elif remaining > 0:
            lines.append(_("  {n} non analizzati (interrotto o illeggibili): ripeti 'indicizza' per riprendere.",
                           n=remaining))
        if stats.engines:
            lines.append(_("  Motori usati: ") + ", ".join(f"{k} {v}" for k, v in
                                                        sorted(stats.engines.items(), key=lambda kv: -kv[1])))
        if stats.errors:
            lines.append(_("  ⚠️  {n} errori, es. {example}", n=len(stats.errors), example=stats.errors[0]))
        mcp = await ctx.sync_mcp(scan.root)
        if mcp:
            lines.append("  " + mcp)
        lines.append("\n" + _("Ora puoi cercare per contenuto: es. 'cerca la ricevuta dell'hotel' "
                               "o chiedere 'quanto ho speso di bollette?'."))
        return CommandResult("\n".join(lines), {
            "root": str(scan.root), "analyzed": stats.analyzed, "skipped_fresh": stats.skipped_fresh,
            "skipped_cloud": stats.skipped_cloud, "remaining": max(0, remaining), "engines": stats.engines,
            "errors": stats.errors[:50], "seconds": round(stats.seconds, 1), "mcp": mcp})
