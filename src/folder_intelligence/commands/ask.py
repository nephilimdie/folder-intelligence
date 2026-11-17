from ..i18n import N_, _
from ..intents import Intent
from .base import Command, CommandResult, Request

QUESTION_START = ("quanto", "quanti", "quante", "quando", "quale", "quali", "chi ", "come ", "perche",
                  "che cosa", "cosa ", "in quale", "ho mai", "c'e un", "c'e una", "esiste",
                  "how much", "how many", "when ", "which ", "who ", "how ", "why ", "what ", "did i ",
                  "do i ", "have i ", "is there", "are there")


class AskCommand(Command):
    name = "ask"
    group = "ricerca"
    description = N_("Risponde a domande sul contenuto dei tuoi documenti, citando i file")
    examples = (N_("quanto ho pagato l'hotel a Roma?"), N_("quando scade il contratto d'affitto?"))
    priority = 45
    params = ("query",)

    def match(self, req: Request):
        if req.norm.endswith("?") or req.norm.startswith(QUESTION_START):
            return {"query": req.text.strip()}
        return None

    async def run(self, ctx, intent: Intent) -> str:
        question = str(intent.args.get("query") or "").strip()
        scan = await ctx.get_scan(intent.path)
        question_text = await ctx.require_index(scan, lambda: self.run(ctx, intent), _("rispondere"))
        if question_text:
            return question_text
        ctx.status(_("Cerco nei documenti …"))
        answer, hits = await ctx.intelligence.ask(question, scan.root, [f.path for f in scan.files])
        if not hits:
            return _("Non ho trovato documenti pertinenti alla domanda.")
        lines = [answer or _("Non riesco a formulare una risposta (AI non disponibile), ma questi file sembrano pertinenti:")]
        lines.append("\n" + _("Fonti:"))
        for i, h in enumerate(hits, 1):
            d = h.doc or {}
            lines.append(f"  [{i}] {self.rel(h.path, scan.root)}" + (f" — {d.get('title')}" if d.get("title") else ""))
        return CommandResult("\n".join(lines), {"question": question, "answer": answer, "sources": [
            {"n": i, "path": h.path, "title": (h.doc or {}).get("title")} for i, h in enumerate(hits, 1)]})
