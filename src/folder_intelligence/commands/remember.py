import re

from ..i18n import N_, _
from ..intents import Intent
from .base import Command, CommandResult, Request


# frasi che sono "cose da fare" (vanno nell'agenda) e non semplici informazioni
_TODO_RE = re.compile(r"^\s*(ricordami di|ricordami che devo|devo|aggiungi(?: all'?\s*agenda)?|da fare:?|"
                      r"remind me to|i need to|i have to|add(?: to (?:my |the )?(?:agenda|to-?do(?: list)?))?|to-?do:?)\b",
                      re.I)
_PREFIX_RE = re.compile(r"^.*?(ricordami( di| che)?|ricorda(ti)? che|prendi nota( che| di)?|annota|"
                        r"aggiungi all'?\s*agenda|add to (?:my |the )?(?:agenda|to-?do(?: list)?)|"
                        r"remind me( to| that)?|remember that|take note( that| of)?|make a note( that| of)?|"
                        r"note that)\s*:?\s*", re.I)


class RememberCommand(Command):
    name = "remember"
    group = "memoria"
    needs_folder = False
    description = N_("Salva una nota o un promemoria (con data: finisce nell'agenda)")
    examples = (N_("ricordami di sistemare le foto"), N_("ricordami di pagare l'affitto venerdì"))
    keywords = ("ricordami", "ricorda che", "ricordati che", "prendi nota", "annota", "aggiungi all'agenda",
                "remind me", "remember that", "take note", "make a note", "note that", "add to my agenda",
                "add to the agenda", "add to my to-do", "add to my todo")
    priority = 20
    params = ("text", "due")
    param_docs = {"due": "due date YYYY-MM-DD, if the user gives one"}

    def match(self, req: Request):
        if not req.has(*self.keywords):
            return None
        note = _PREFIX_RE.sub("", req.text, count=1).strip()
        return {"text": note or req.text, "todo": bool(_TODO_RE.search(req.text)) or None, "path": None}

    async def run(self, ctx, intent: Intent) -> str:
        from datetime import date

        from .. import agenda
        from ..dates import parse_due

        note = str(intent.args.get("text", "")).strip()
        if not note:
            return _("Cosa devo ricordare?")
        due, what = parse_due(note)
        if intent.args.get("due"):
            try:
                due = date.fromisoformat(str(intent.args["due"])[:10])
            except ValueError:
                pass
        todo = intent.args.get("todo") or due is not None or _TODO_RE.search(note) is not None
        await ctx.memory.save("note", note, {"directory": str(ctx.directory)})
        if not todo:
            return _("📝 Ok, me lo ricordo: \"{note}\"", note=note)
        item = agenda.add(what or note, due)
        when = agenda.format_due(item.due_date)
        msg = (_("📅 Aggiunto all'agenda (#{id}): {text} — {when}", id=item.id, text=item.text, when=when) if when
               else _("📅 Aggiunto all'agenda (#{id}): {text}", id=item.id, text=item.text))
        return CommandResult(msg, {"item": item.to_dict()})
