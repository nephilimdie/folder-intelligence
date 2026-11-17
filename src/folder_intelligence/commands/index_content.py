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
        args: dict = {}
        m = req.search(r"\b(?:primi|max|massimo|first|maximum|up to)\s+(\d{1,5})\b")
        if m:
            args["limit"] = int(m.group(1))
        if req.has("veloce", "rapid", "solo testo", "fast", "quick"):
            args["fast"] = True
        return args

    async def run(self, ctx, intent: Intent) -> str:
        """Due tempi: 1) passo veloce (testo, OCR, embedding) → la ricerca funziona subito;
        2) comprensione con l'AI (riassunti, immagini) sui file già cercabili."""
        scan = await ctx.get_scan(intent.path)
        limit = int(intent.args.get("limit") or ctx.index_limit or 0) or None  # None = tutti
        engine = ctx.intelligence
        ai = ctx.ai
        ctx.status(_("Leggo i testi dei file …"))
        files = sorted((f for f in scan.files), key=lambda f: -f.modified)  # i più recenti prima
        fast = await engine.analyze_many([f.path for f in files], limit=limit, deep=False,
                                         progress=ctx.progress, cancel=ctx.cancel)
        engine.index.remove_missing(scan.root)
        scan.classified = False
        searchable = engine.index.stats(scan.root)["documents"]
        notify = getattr(ctx, "notify", None)
        if notify:
            notify(_("🔎 Ricerca pronta su {n} file di {root}: puoi già cercare e fare domande.",
                     n=searchable, root=scan.root))

        deep = None
        ai_ok = bool(ai) and await ai.is_available()
        if ai_ok and not intent.args.get("fast"):
            ctx.status(_("Comprendo i contenuti con l'AI ({model}) …", model=engine.index_model or ai.model))
            deep = await engine.enrich(scan.root, progress=ctx.progress, cancel=ctx.cancel)
        pending = len(engine.pending_enrichment(scan.root))

        lines = [_("🔎 {n} file cercabili in {root} (letti ora: {new}, in {s}s).", n=searchable, root=scan.root,
                   new=fast.analyzed, s=f"{fast.seconds:.0f}")]
        if deep is not None:
            lines.append(_("🧠 Compresi con l'AI: {n} file in {s}s.", n=deep.analyzed, s=f"{deep.seconds:.0f}"))
        if pending:
            lines.append(_("  {n} file aspettano ancora la comprensione con l'AI: la ricerca li trova già; "
                           "si completano ripetendo 'indicizza' o da soli col servizio in background "
                           "(folder-intelligence --install-background).", n=pending))
        if fast.skipped_fresh:
            lines.append(_("  {n} già in indice e non modificati.", n=fast.skipped_fresh))
        if fast.skipped_noncontent:
            lines.append(_("  {n} file di sistema o di app saltati (non sono documenti).", n=fast.skipped_noncontent))
        if fast.skipped_cloud:
            lines.append(_("  ⛅ {n} solo su iCloud: saltati (scaricali per analizzarli).", n=fast.skipped_cloud))
        remaining = (len(scan.files) - fast.analyzed - fast.skipped_fresh - fast.skipped_cloud
                     - fast.skipped_noncontent)
        if remaining > 0 and limit:
            lines.append(_("  {n} ancora da analizzare: ripeti 'indicizza' per continuare (massimo {limit} per volta).",
                           n=remaining, limit=limit))
        elif remaining > 0:
            lines.append(_("  {n} non analizzati (interrotto o illeggibili): ripeti 'indicizza' per riprendere.",
                           n=remaining))
        errors = fast.errors + (deep.errors if deep else [])
        if errors:
            lines.append(_("  ❗ {n} errori, es. {example}", n=len(errors), example=errors[0]))
        mcp = await ctx.sync_mcp(scan.root)
        if mcp:
            lines.append("  " + mcp)
        if not ai_ok:
            lines.append(_("  (AI non attiva: ricerca per testo e significato, senza riassunti.)"))
        lines.append("\n" + _("Ora puoi cercare per contenuto: es. 'cerca la ricevuta dell'hotel' "
                               "o chiedere 'quanto ho speso di bollette?'."))
        return CommandResult("\n".join(lines), {
            "root": str(scan.root), "analyzed": fast.analyzed, "searchable": searchable,
            "enriched": deep.analyzed if deep else 0, "pending_enrichment": pending,
            "skipped_fresh": fast.skipped_fresh, "skipped_cloud": fast.skipped_cloud,
            "skipped_noncontent": fast.skipped_noncontent, "remaining": max(0, remaining),
            "engines": fast.engines, "errors": errors[:50],
            "seconds": round(fast.seconds + (deep.seconds if deep else 0), 1), "mcp": mcp})
