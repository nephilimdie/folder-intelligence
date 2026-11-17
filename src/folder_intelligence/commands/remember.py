import re

from ..i18n import N_, _
from ..intents import Intent
from .base import Command, CommandResult, Request


# frasi informative ("ricordati CHE le bollette…"): restano note. Tutto il resto è una cosa da fare.
_INFO_RE = re.compile(r"\b(ricorda(?:ti)? che|tieni a mente che|prendi nota che|annota che|"
                      r"remember that|keep in mind that|note that|take note that)\b", re.I)
_PREFIX_RE = re.compile(r"^.*?(ricordami( di| che)?|mi ricordi( di)?|mi puoi ricordare( di)?|puoi ricordarmi( di)?|"
                        r"ricordarmi( di)?|ricorda(ti)? che|tieni a mente che|prendi nota( che| di)?|annota( che)?|"
                        r"aggiungi all'?\s*agenda|add to (?:my |the )?(?:agenda|to-?do(?: list)?)|"
                        r"remind me( to| that)?|can you remind me( to)?|remember that|keep in mind that|"
                        r"take note( that| of)?|make a note( that| of)?|note that)\s*:?\s*", re.I)
_POLITE_RE = re.compile(r"[\s,]*(per\s*favore|perfavore|grazie|please|thanks|thank you)[\s?!.]*$", re.I)


class RememberCommand(Command):
    name = "remember"
    group = "memoria"
    needs_folder = False
    description = N_("Salva un promemoria nell'agenda (con data e ora, o tra 6 ore) o una nota")
    examples = (N_("ricordami di sistemare le foto"), N_("ricordami di pagare l'affitto venerdì alle 10"))
    keywords = ("ricordami", "mi ricordi", "ricordarmi", "mi puoi ricordare", "ricorda che", "ricordati che",
                "tieni a mente", "prendi nota", "annota", "aggiungi all'agenda", "remind me", "remember that",
                "keep in mind", "take note", "make a note", "note that", "add to my agenda", "add to the agenda",
                "add to my to-do", "add to my todo")
    priority = 20
    params = ("text", "due")
    param_docs = {"text": "the thing to remember, without the date/time words",
                  "due": "due date YYYY-MM-DD or YYYY-MM-DDTHH:MM, only if the user gives one"}

    def match(self, req: Request):
        if not req.has(*self.keywords):
            return None
        note = _PREFIX_RE.sub("", req.text, count=1).strip()
        return {"text": note or req.text, "todo": not _INFO_RE.search(req.text), "path": None}

    async def run(self, ctx, intent: Intent) -> str:
        from datetime import date, datetime

        from .. import agenda
        from ..dates import parse_when

        raw = str(intent.args.get("text", "")).strip()
        note = _POLITE_RE.sub("", _PREFIX_RE.sub("", raw, count=1)).strip(" ?!.") or raw
        if not note:
            return _("Cosa devo ricordare?")
        await ctx.memory.save("note", note, {"directory": str(ctx.directory)})
        # una nota solo se la frase era informativa ("ricordati che …"); altrimenti va in agenda
        if intent.args.get("todo") is False or _INFO_RE.search(raw):
            return _("📝 Ok, me lo ricordo: \"{note}\"", note=note)
        when, what = parse_when(note)
        given = str(intent.args.get("due") or "").strip()
        if given:
            try:
                when = datetime.fromisoformat(given) if "T" in given else date.fromisoformat(given[:10])
            except ValueError:
                pass
        item = agenda.add(what or note, when)
        msg = _("📅 Aggiunto all'agenda (#{id}): {text} — {when}", id=item.id, text=item.text,
                when=agenda.format_due(item.when))
        cal = await ctx.blocking(agenda.calendar_add, item)  # evento nel Calendario del Mac
        if cal:
            msg += "\n" + cal
        if item.status() == "overdue":  # "… ieri": lo salviamo, ma lo diciamo
            msg += "\n" + _("❗ Attenzione: la data è già passata. Per cambiarla: 'elimina promemoria {id}' e "
                            "riscrivilo con la data giusta.", id=item.id)
        return CommandResult(msg, {"item": item.to_dict()})
