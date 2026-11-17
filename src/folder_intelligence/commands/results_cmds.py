"""Comandi sui risultati dell'ultima ricerca: filtri a parole, Finder, ricerche salvate."""

import re
from pathlib import Path

from .. import results
from ..i18n import N_, _
from ..intents import Intent
from .base import Command, CommandResult, Request


def _no_results() -> str:
    return _("Prima fai una ricerca, es. 'cerca la ricevuta dell'hotel'.")


def results_reply(rs: results.ResultSet, headline: str) -> CommandResult:
    """Risposta con l'elenco (chat classica) e i dati per il riquadro Risultati."""
    visible = rs.visible()
    lines = [headline]
    for h in visible[:15]:
        lines.append(f"  • {h.title}  ({Path(h.path).parent.name}/{h.name})")
    if len(visible) > 15:
        lines.append(_("  … e altri {n}", n=len(visible) - 15))
    return CommandResult("\n".join(lines), {"results": rs.to_dict(), "open_results": True,
                                            "summary": headline})


class RefineResultsCommand(Command):
    name = "refine_results"
    group = "ricerca"
    needs_folder = False
    description = N_("Filtra o riordina i risultati dell'ultima ricerca (tipo, data, cartella, ordine)")
    examples = (N_("solo i pdf"), N_("ordina per data"))
    priority = 15
    params = ("text",)
    _START = re.compile(r"^(?:e\s+|and\s+)?(?:solo|soltanto|only|ordina|ordinali|sort|togli quelli|escludi|tranne|"
                        r"senza quelli|exclude|remove those|mostrali tutti|tutti i risultati|togli i filtri|"
                        r"show all|clear filters|(?:del|dal|nel|from|of)\s+(?:19|20)\d\d$|"
                        r"(?:quelli\s+|those\s+)?(?:sopra|sotto|oltre|piu di|meno di|tra|fra|above|over|under|below|"
                        r"more than|less than|between)\s+(?:i\s+|the\s+)?(?:€\s*)?\d)")

    def match(self, req: Request):
        norm = req.norm.strip(" ?!.")
        if "agenda" in norm or "promemoria" in norm or "reminder" in norm:
            return None
        return {"text": req.text, "path": None} if self._START.search(norm) else None

    async def run(self, ctx, intent: Intent) -> str:
        rs = ctx.last_results
        if rs is None:
            return _no_results()
        changed = rs.refine(str(intent.args.get("text", "")))
        if not changed:
            return _("Non ho capito come filtrare. Prova: 'solo i pdf', 'del 2024', 'togli quelli in Downloads', "
                     "'ordina per data', 'mostrali tutti'.")
        desc = rs.filters.describe()
        n = len(rs.visible())
        head = (_("🔎 «{query}»: {n} file ({filters}).", query=rs.query, n=n, filters=desc) if desc
                else _("🔎 «{query}»: tutti i {n} file.", query=rs.query, n=n))
        return results_reply(rs, head)


class ResultsToFinderCommand(Command):
    name = "results_to_finder"
    group = "ricerca"
    needs_folder = False
    description = N_("Mette i risultati nel Finder: etichetta (o cartella di alias, vedi impostazioni)")
    examples = (N_("metti i risultati nel Finder"), N_("etichetta questi risultati come Bollette"))
    priority = 15
    params = ("name", "mode")

    def match(self, req: Request):
        norm = req.norm
        if not req.has("risultat", "result", "questi file", "these files", "etichett", "tag them", "label them"):
            return None
        if not req.has("finder", "etichett", "tag", "label", "cartella", "folder", "metti", "put", "salva i risultati",
                       "save the results"):
            return None
        if req.has("salva questa ricerca", "salva la ricerca", "save this search", "save the search"):
            return None
        if re.match(r"^(togli|rimuovi|elimina|remove|delete)\b", norm):
            return None  # "togli l'etichetta X" è un altro comando
        m = re.search(r"\b(?:come|chiamat[ao]|nome|as|called|named)\s+[\"«']?(.+?)[\"»']?\s*$", req.text, re.I)
        mode = "aliases" if re.search(r"\b(cartella|folder)\b", norm) and "finder" not in norm else None
        return {"name": m.group(1).strip() if m else None, "mode": mode, "path": None}

    async def run(self, ctx, intent: Intent) -> str:
        rs = ctx.last_results
        if rs is None:
            return _no_results()
        paths = [h.path for h in rs.visible()]
        name = intent.args.get("name") or rs.query
        res = await ctx.blocking(results.to_finder, paths, name, intent.args.get("mode"))
        results.save_search(rs, results.safe_name(name), destination=res["mode"], paths=paths)
        if res["mode"] == "aliases":
            msg = _("📂 Creata la cartella «{name}» con {n} collegamenti ai file (gli originali restano dove sono): "
                    "{folder}", name=res["name"], n=res["added"], folder=res["folder"])
        else:
            msg = _("📌 {n} file etichettati «{name}» nel Finder (nessun file spostato). Li trovi nella barra laterale "
                    "del Finder sotto Etichette, o cercando l'etichetta.", n=res["tagged"], name=res["name"])
        msg += "\n" + _("Ricerca salvata come «{name}»: i file nuovi che le corrispondono si aggiungeranno da soli "
                        "(col servizio in background). Per toglierla: 'togli l'etichetta {name}'.", name=res["name"])
        if res["failed"]:
            msg += "\n" + _("❗ {n} file non riusciti, es. {example}", n=len(res["failed"]), example=res["failed"][0])
        return CommandResult(msg, {"finder": res})


class SaveSearchCommand(Command):
    name = "save_search"
    group = "ricerca"
    needs_folder = False
    description = N_("Salva l'ultima ricerca con un nome, per ritrovarla e aggiornarla")
    examples = (N_("salva questa ricerca come Viaggi"),)
    priority = 15
    params = ("name",)

    def match(self, req: Request):
        m = re.search(r"\b(?:salva|save)\s+(?:questa|la|this|the)\s+(?:ricerca|search)(?:\s+(?:come|as|con nome|named)"
                      r"\s+[\"«']?(.+?)[\"»']?)?\s*$", req.text, re.I)
        return {"name": (m.group(1) or "").strip() or None, "path": None} if m else None

    async def run(self, ctx, intent: Intent) -> str:
        rs = ctx.last_results
        if rs is None:
            return _no_results()
        entry = results.save_search(rs, results.safe_name(intent.args.get("name") or rs.query))
        return _("💾 Ricerca salvata come «{name}». Per riaprirla: 'apri la ricerca {name}'.", name=entry.name)


class SavedSearchesCommand(Command):
    name = "saved_searches"
    group = "ricerca"
    needs_folder = False
    description = N_("Ricerche salvate e recenti: elenca, riapri o elimina")
    examples = (N_("ricerche salvate"), N_("apri la ricerca Viaggi"))
    priority = 15
    params = ("name", "action")

    def match(self, req: Request):
        m = re.search(r"\b(?:apri|mostra|riapri|rifai|open|show|run)\s+(?:la\s+|the\s+)?(?:ricerca|search)\s+"
                      r"[\"«']?(.+?)[\"»']?\s*$", req.text, re.I)
        if m:
            return {"name": m.group(1).strip(), "action": "open", "path": None}
        m = re.search(r"\b(?:elimina|cancella|delete|remove)\s+(?:la\s+|the\s+)?(?:ricerca salvata|saved search|ricerca)"
                      r"\s+[\"«']?(.+?)[\"»']?\s*$", req.text, re.I)
        if m:
            return {"name": m.group(1).strip(), "action": "delete", "path": None}
        m = re.search(r"\b(?:togli|rimuovi|remove)\s+(?:l'?\s*)?(?:etichetta|tag|label)\s+[\"«']?(.+?)[\"»']?\s*$",
                      req.text, re.I)
        if m:
            return {"name": m.group(1).strip(), "action": "untag", "path": None}
        if req.has("ricerche salvate", "saved searches"):
            return {"action": "list", "path": None}
        if req.has("ricerche recenti", "cronologia ricerche", "cronologia delle ricerche", "recent searches",
                   "search history"):
            return {"action": "recent", "path": None}
        return None

    async def run(self, ctx, intent: Intent) -> str:
        action = intent.args.get("action", "list")
        name = str(intent.args.get("name") or "")
        if action == "recent":
            hist = results.history()
            if not hist:
                return _("Nessuna ricerca recente.")
            lines = [_("Ricerche recenti:")] + [f"  • «{h['query']}» — {h['count']} " + _("file") for h in hist]
            return CommandResult("\n".join(lines), {"history": hist})
        if action == "list":
            items = results.saved()
            if not items:
                return _("Nessuna ricerca salvata. Dopo una ricerca: 'salva questa ricerca come …'.")
            lines = [_("Ricerche salvate:")]
            for s in items:
                where = {"tag": _("etichetta del Finder"), "aliases": _("cartella di alias")}.get(s.destination or "", "")
                lines.append(f"  • {s.name} — «{s.query}»" + (f" · {where}, {len(s.paths)} " + _("file") if where else ""))
            lines.append(_("Per riaprirne una: 'apri la ricerca <nome>'."))
            return CommandResult("\n".join(lines), {"saved": [s.__dict__ for s in items]})
        entry = results.get_saved(name)
        if entry is None:
            return _("Non trovo una ricerca salvata «{name}». Scrivi 'ricerche salvate' per l'elenco.", name=name)
        if action == "delete":
            results.delete_saved(entry.name)
            return _("Ricerca «{name}» eliminata (i file non sono stati toccati).", name=entry.name)
        if action == "untag":
            n = await ctx.blocking(results.remove_from_finder, entry.paths, entry.name)
            entry.paths, entry.destination = [], None
            results.update_saved(entry)
            return _("Etichetta «{name}» tolta da {n} file.", name=entry.name, n=n)
        rs = await results.run_saved(entry, ctx.intelligence)
        ctx.last_results = rs
        return results_reply(rs, _("🔎 Ricerca «{name}»: {n} file.", name=entry.name, n=len(rs.visible())))
