import re

from .. import agenda
from ..i18n import N_, _
from ..intents import Intent
from .base import Command, CommandResult, Request

_REF = r"#?\s*(\d{1,4})"


def item_line(item: agenda.Item) -> str:
    when = agenda.format_due(item.when)
    mark = {"overdue": "⚠️ ", "today": "🔔 ", "done": "✅ "}.get(item.status(), "• ")
    return f"  {mark}#{item.id}  " + (f"{when} — " if when else "") + item.text


class AgendaCommand(Command):
    name = "agenda"
    group = "memoria"
    needs_folder = False
    description = N_("Mostra l'agenda: le cose da fare, con la data se c'è")
    examples = (N_("cosa devo fare?"), N_("mostra l'agenda"))
    keywords = ("agenda", "cosa devo fare", "cose da fare", "promemoria", "to-do", "todo", "to do list",
                "what do i have to do", "my reminders", "reminders")
    priority = 18

    def match(self, req: Request):
        if req.search(r"\b(elimina|cancella|rimuovi|togli|delete|remove|aggiungi|ricordami|add|remind)\b"):
            return None  # aggiungere o togliere voci sono altri comandi
        return {} if req.has(*self.keywords) else None

    async def run(self, ctx, intent: Intent) -> str:
        todo = agenda.pending()
        done = agenda.recently_done()
        if not todo and not done:
            return CommandResult(_("L'agenda è vuota. Aggiungi qualcosa con 'ricordami di … venerdì'."),
                                 {"items": []})
        lines = [_("📅 Agenda:")]
        lines += [item_line(i) for i in todo] or [_("  niente da fare 🎉")]
        if done:
            lines.append(_("Fatti di recente:"))
            lines += [item_line(i) for i in done]
        lines.append("\n" + _("'fatto 2' segna come fatto · 'elimina promemoria 2' lo toglie"))
        return CommandResult("\n".join(lines), {"items": [i.to_dict() for i in todo + done]})


class AgendaDoneCommand(Command):
    name = "agenda_done"
    group = "memoria"
    needs_folder = False
    description = N_("Segna come fatta una voce dell'agenda (o la riapre)")
    examples = (N_("fatto 2"),)
    priority = 17
    params = ("ref", "done")

    def match(self, req: Request):
        norm = req.norm.strip(" .!")
        m = req.search(rf"^(?:fatto|fatta|completato|completata|segna(?: come fatto)?|done|completed|finished|"
                       rf"mark(?: as done)?)\s+(?:il |la |the |item |voce |promemoria )?{_REF}(?:\s+(?:come fatto|as done))?$")
        if m:
            return {"ref": m.group(1), "done": True, "path": None}
        m = re.search(rf"^(?:riapri|reopen|non fatto|undone)\s+{_REF}$", norm)
        if m:
            return {"ref": m.group(1), "done": False, "path": None}
        return None

    async def run(self, ctx, intent: Intent) -> str:
        ref = intent.args.get("ref")
        done = intent.args.get("done", True) not in (False, "false", "0")
        item = agenda.set_done(ref, done) if ref is not None else None
        if item is None:
            return _("Nell'agenda non c'è la voce #{ref}. Scrivi 'agenda' per vederla.", ref=ref)
        msg = (_("✅ Fatto: {text}", text=item.text) if done else _("↩️  Riaperto: {text}", text=item.text))
        return CommandResult(msg, {"item": item.to_dict()})


class AgendaRemoveCommand(Command):
    name = "agenda_remove"
    group = "memoria"
    needs_folder = False
    description = N_("Toglie una voce dall'agenda")
    examples = (N_("elimina promemoria 2"),)
    priority = 16
    params = ("ref",)
    destructive = False

    def match(self, req: Request):
        m = req.search(rf"\b(?:elimina|cancella|rimuovi|togli|delete|remove)\s+(?:il |la |the )?"
                       rf"(?:promemoria|voce|reminder|item|to-?do)\s+{_REF}\b")
        if m:
            return {"ref": m.group(1), "path": None}
        m = req.search(rf"\b(?:togli|rimuovi|remove|delete)\s+{_REF}\s+(?:dall'?\s*agenda|from (?:the |my )?agenda)\b")
        return {"ref": m.group(1), "path": None} if m else None

    async def run(self, ctx, intent: Intent) -> str:
        ref = intent.args.get("ref")
        item = agenda.remove(ref) if ref is not None else None
        if item is None:
            return _("Nell'agenda non c'è la voce #{ref}. Scrivi 'agenda' per vederla.", ref=ref)
        return CommandResult(_("🗑️  Tolto dall'agenda: {text}", text=item.text), {"removed": item.to_dict()})

